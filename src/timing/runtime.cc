#include "timing/runtime.h"
#include <set>
#include <stdexcept>

namespace ndpsim::timing {
struct Record {
    const Runtime* runtime;
    uint64_t id, owners = 1, pins = 1, callbacks = 0, pending = 0, ticket = 0;
    State state = State::Submitted;
    Operation op;
    std::vector<std::shared_ptr<Record>> inputs;
    std::vector<Done> waiters;
    std::optional<Failure> failure;
    bool terminal_queued = false;
};
uint64_t CompletionToken::id() const { return record_ ? record_->id : 0; }
Runtime::Runtime(EventBridge& b, EventBridge::Clock n) : bridge_(b), now_(std::move(n)) {
    if (!now_) throw std::invalid_argument("runtime clock required");
}
std::shared_ptr<Record> Runtime::checked(CompletionToken t) const {
    if (!t.record_ || t.record_->runtime != this || !t.lease_ || !*t.lease_ ||
        !tasks_.count(t.id()) || tasks_.at(t.id()) != t.record_)
        throw std::invalid_argument("invalid/released completion token");
    return t.record_;
}
void Runtime::transition(const std::shared_ptr<Record>& r, State state) {
    if (r->state == State::WaitingDependencies) dependency_waiters_.erase(r->id);
    r->state = state;
    if (state == State::WaitingDependencies) dependency_waiters_.insert(r->id);
    transitions_.push_back({now_(), r->id, state});
}
void Runtime::defer(const std::shared_ptr<Record>& r, EventBridge::Phase phase,
                    std::function<void()> action) {
    ++r->callbacks;
    bridge_.enqueue(now_(), phase, "metadata task=" + std::to_string(r->id),
                    [this, r, action = std::move(action)] {
        try { action(); }
        catch (...) { --r->callbacks; collect(r); throw; }
        --r->callbacks;
        collect(r);
    });
}
CompletionToken Runtime::submit(Operation op) {
    if (op.key.op.empty() || op.key.stage.empty() || !op.resource)
        throw std::invalid_argument("incomplete metadata operation");
    checked_add(now_(), op.duration_ns);
    for (const auto* counters : {&op.work, &op.features})
        for (const auto& c : *counters)
            if (c.first.empty() || c.second.second.empty())
                throw std::invalid_argument("empty Work/feature name/unit");
    std::set<std::string> ports;
    for (const auto& o : op.outputs)
        if (o.port.empty() || o.type.empty() || !ports.insert(o.port).second)
            throw std::invalid_argument("invalid output description");
    std::vector<std::shared_ptr<Record>> inputs;
    std::set<uint64_t> seen;
    for (const auto& token : op.inputs) {
        auto r = checked(token);
        if (r->op.key.query != op.key.query) throw std::invalid_argument("cross-query dependency");
        if (seen.insert(r->id).second) inputs.push_back(r);
    }
    auto id = checked_add(next_, 1);
    auto r = std::make_shared<Record>();
    r->runtime = this;
    r->id = id;
    r->inputs = std::move(inputs);
    op.inputs.clear(); // Pins, rather than caller leases, own dependency metadata.
    r->op = std::move(op);
    for (auto& input : r->inputs) ++input->pins;
    tasks_.emplace(id, r);
    next_ = id;
    transition(r, State::Submitted);
    defer(r, EventBridge::ContinueSubmit, [this, r] {
        if (r->state == State::FailedDraining || r->state == State::Failed) return;
        transition(r, State::WaitingDependencies);
        ready(r);
    });
    CompletionToken t;
    t.record_ = r;
    t.lease_ = std::make_shared<bool>(true);
    return t;
}
CompletionToken Runtime::retain(CompletionToken t) {
    auto r = checked(t);
    r->owners = checked_add(r->owners, 1);
    t.lease_ = std::make_shared<bool>(true);
    return t;
}
void Runtime::release(CompletionToken t) {
    auto r = checked(t);
    *t.lease_ = false;
    --r->owners;
    collect(r);
}
State Runtime::state(CompletionToken t) const { return checked(t)->state; }
std::vector<capture::Output> Runtime::describe(CompletionToken t) const {
    auto r = checked(t);
    if (r->state != State::Visible) throw std::logic_error("metadata output not visible");
    return r->op.outputs;
}
void Runtime::when_complete(CompletionToken t, Done done) {
    if (!done) throw std::invalid_argument("empty continuation");
    auto r = checked(t);
    if (r->state == State::Visible || r->state == State::Failed) {
        defer(r, EventBridge::ContinueSubmit, [r, done = std::move(done)] { done({r->failure}); });
    } else r->waiters.push_back(std::move(done));
}
void Runtime::ready(const std::shared_ptr<Record>& r) {
    if (r->state != State::WaitingDependencies) return;
    bool all_visible = true;
    for (auto& input : r->inputs) {
        if (input->state == State::Failed) { failing(r, *input->failure); return; }
        all_visible = all_visible && input->state == State::Visible;
    }
    if (!all_visible) return;
    transition(r, State::WaitingResource);
    if (r->op.input) adapter(r, r->op.input, [this, r] { compute(r); });
    else compute(r);
}
void Runtime::adapter(const std::shared_ptr<Record>& r, Adapter start,
                      std::function<void()> success) {
    ++r->pending;
    auto called = std::make_shared<bool>(false);
    Done done = [this, r, called, success = std::move(success)](Outcome outcome) {
        if (*called) throw std::logic_error("duplicate adapter completion");
        *called = true;
        // Even a synchronous/zero-service adapter cannot release a successor inline.
        defer(r, EventBridge::ResourceCompleteRelease, [this, r, outcome, success] {
            --r->pending;
            if (outcome.failure && r->state != State::FailedDraining)
                failing(r, {r->id, outcome.failure->code, outcome.failure->detail});
            if (r->state == State::FailedDraining) terminal(r);
            else defer(r, EventBridge::ContinueSubmit, success);
        });
    };
    try { start(done); }
    catch (const std::exception& e) {
        if (*called) throw; // Invalid adapter: completion followed by throw.
        done({Failure{r->id, "AdapterFailure", e.what()}});
    }
}
void Runtime::compute(const std::shared_ptr<Record>& r) {
    if (r->state == State::FailedDraining || r->state == State::Failed) return;
    ++r->pending;
    try {
        r->ticket = r->op.resource->submit(r->op.key.op + "/" + r->op.key.stage,
            r->op.duration_ns,
            [this, r] { transition(r, State::Running); },
            [this, r](bool ran) {
                r->ticket = 0;
                --r->pending;
                if (r->state == State::FailedDraining) terminal(r);
                else if (!ran) failing(r, {r->id, "Cancelled", "resource request cancelled"});
                else service_done(r);
                collect(r);
            });
    } catch (const std::exception& e) {
        --r->pending;
        failing(r, {r->id, "ResourceFailure", e.what()});
    }
}
void Runtime::service_done(const std::shared_ptr<Record>& r) {
    if (r->state != State::Running) throw std::logic_error("service completion out of order");
    transition(r, State::ServiceDone);
    // Inputs are no longer in use after real compute completion.
    for (auto& input : r->inputs) { --input->pins; collect(input); }
    r->inputs.clear();
    if (r->op.send_return) {
        defer(r, EventBridge::ContinueSubmit, [this, r] {
            if (r->state == State::FailedDraining || r->state == State::Failed) return;
            transition(r, State::WaitingReturn);
            adapter(r, r->op.send_return, [this, r] { terminal(r); });
        });
    } else terminal(r);
}
void Runtime::fail(CompletionToken t, std::string code, std::string detail) {
    auto r = checked(t);
    if (code.empty()) throw std::invalid_argument("empty failure code");
    if (r->state == State::Visible || r->state == State::Failed || r->state == State::FailedDraining)
        throw std::logic_error("duplicate/terminal failure");
    failing(r, {r->id, std::move(code), std::move(detail)});
}
void Runtime::failing(const std::shared_ptr<Record>& r, Failure failure) {
    r->failure = std::move(failure);
    transition(r, State::FailedDraining);
    if (r->ticket) r->op.resource->cancel(r->ticket);
    terminal(r);
}
void Runtime::terminal(const std::shared_ptr<Record>& r) {
    if (r->pending || r->terminal_queued) return;
    r->terminal_queued = true;
    // Failure cleanup is also phase1, followed by phase2 failure publication.
    defer(r, EventBridge::ResourceCompleteRelease, [this, r] {
        for (auto& input : r->inputs) { --input->pins; collect(input); }
        r->inputs.clear();
        defer(r, EventBridge::Publish, [this, r] { publish(r); });
    });
}
void Runtime::publish(const std::shared_ptr<Record>& r) {
    if (r->pending) throw std::logic_error("publication before drain");
    transition(r, r->failure ? State::Failed : State::Visible);
    --r->pins;
    r->op.input = {};
    r->op.send_return = {};
    auto waiters = std::move(r->waiters);
    for (auto& done : waiters)
        defer(r, EventBridge::ContinueSubmit, [r, done = std::move(done)] { done({r->failure}); });
    // Copy ready records: failure propagation may reclaim entries during callbacks.
    std::vector<std::shared_ptr<Record>> waiting;
    for (auto id : dependency_waiters_)
        waiting.push_back(tasks_.at(id));
    for (auto& w : waiting)
        defer(w, EventBridge::ContinueSubmit, [this, w] { ready(w); });
}
void Runtime::collect(const std::shared_ptr<Record>& r) {
    if (!r->owners && !r->pins && !r->callbacks && !r->pending &&
        (r->state == State::Visible || r->state == State::Failed)) {
        tasks_.erase(r->id);
        r->op = {}; // Released token copies cannot retain completed metadata.
    }
}
size_t Runtime::owners() const {
    size_t n = 0;
    for (const auto& entry : tasks_) n += entry.second->owners;
    return n;
}
size_t Runtime::pins() const {
    size_t n = 0;
    for (const auto& entry : tasks_) n += entry.second->pins;
    return n;
}
void Runtime::require_progress() const {
    if (!bridge_.idle()) return; // Only an empty bridge can be stalled.
    size_t unfinished = 0;
    std::string diagnostic;
    for (const auto& entry : tasks_) {
        auto& r = *entry.second;
        if (r.state == State::Visible || r.state == State::Failed) continue;
        ++unfinished;
        diagnostic += " task=" + std::to_string(r.id) + " state=" +
            std::to_string(static_cast<int>(r.state)) + " pending=" + std::to_string(r.pending);
    }
    bridge_.require_progress(unfinished, diagnostic);
}
} // namespace ndpsim::timing
