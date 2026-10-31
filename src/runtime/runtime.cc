#include "runtime/runtime.h"
#include <sstream>

namespace ndpsim::runtime {
struct Record {
    const Runtime* owner;
    uint64_t id, query;
    Location location;
    size_t owners = 1, pins = 1;
    bool visible = false, reclaimed = false;
    std::optional<Bytes> output;
    std::optional<Failure> failure;
};
uint64_t Handle::id() const {
    return record_ ? record_->id : 0;
}
const char* name(State s) {
    static const char* names[] = {"Submitted",
                                  "WaitingDependencies",
                                  "WaitingResource",
                                  "Running",
                                  "ServiceDone",
                                  "WaitingReturn",
                                  "Visible",
                                  "Failed"};
    return names[static_cast<unsigned>(s)];
}
std::shared_ptr<Record> Runtime::checked(Handle h) const {
    if (h.record_ && h.record_->owner == this && h.active_ && !*h.active_)
        throw content::Error(Code::Released, "runtime result lease", {}, h.id());
    if (!h.record_ || h.record_->owner != this || !tasks_.count(h.id()) ||
        tasks_.at(h.id()).result != h.record_)
        throw content::Error(Code::InvalidHandle, "runtime result token");
    if (!h.active_ || !*h.active_)
        throw content::Error(Code::Released, "runtime result lease", {}, h.id());
    return h.record_;
}
Runtime::Task& Runtime::task(uint64_t id) {
    auto it = tasks_.find(id);
    if (it == tasks_.end())
        throw content::Error(Code::InvalidHandle, "unknown task", {0, id, now_()});
    return it->second;
}
void Runtime::transition(Task& t, State s) {
    t.state = s;
    transitions_.push_back({now_(), t.op.query, t.result->id, s});
}
void Runtime::defer(uint64_t id,
                    EventBridge::Phase phase,
                    std::string cause,
                    std::function<void()> action) {
    auto r = task(id).result;
    ++task(id).callbacks;
    bridge_.enqueue(now_(), phase, std::move(cause), [this, id, r, action = std::move(action)] {
        try {
            action();
        } catch (...) {
            --task(id).callbacks;
            collect(r);
            throw;
        }
        --task(id).callbacks;
        collect(r);
    });
}
Handle Runtime::submit(Operation op) {
    if (!op.query || !op.evaluate || !op.grant || !op.service ||
        (op.needs_return && (!op.send_return || op.location != Location::HostMemory)) ||
        op.location == Location::SSDStorage)
        throw content::Error(Code::InvalidArgument,
                             "incomplete operation/unsupported result location");
    for (auto h : op.inputs)
        checked(h);              // Validate atomically before allocating or pinning.
    static uint64_t next_id = 1; // One rank/thread; IDs are never reused across runtimes.
    if (next_id == UINT64_MAX)
        throw content::Error(Code::Overflow, "task ID overflow");
    uint64_t id = next_id++;
    auto r = std::make_shared<Record>();
    r->owner = this;
    r->id = id;
    r->query = op.query;
    r->location = op.location;
    auto& t = tasks_.emplace(id, Task{std::move(op), r}).first->second;
    peak_tasks_ = std::max(peak_tasks_, tasks_.size());
    transition(t, State::Submitted);
    // Independent input ownership survives caller release until functional execution ends.
    for (auto& h : t.op.inputs)
        h = retain(h);
    Handle h;
    h.record_ = r;
    h.active_ = std::make_shared<bool>(true);
    defer(id, EventBridge::ContinueSubmit, "submit task=" + std::to_string(id), [this, id] {
        if (!tasks_.count(id))
            return;
        auto& t = task(id);
        if (t.state == State::Failed)
            return;
        transition(t, State::WaitingDependencies);
        ++t.callbacks; // dependency callback, including its queued continuation
        t.wait = wait(t.op.inputs, id, [this, id](Outcome o) {
            auto& t = task(id);
            --t.callbacks;
            t.wait = 0;
            if (t.state == State::Failed) {
                collect(t.result);
                return;
            }
            if (o.failure) {
                failed(id, *o.failure);
                return;
            }
            transition(t, State::WaitingResource);
            try {
                t.op.grant(id);
            } catch (const content::Error& e) {
                fail(id, e.code, e.what());
            } catch (const std::exception& e) {
                fail(id, Code::BackendFailure, e.what());
            }
        });
    });
    return h;
}
Handle Runtime::retain(Handle h) {
    auto r = checked(h);
    ++r->owners;
    h.active_ = std::make_shared<bool>(true);
    return h;
}
void Runtime::collect(std::shared_ptr<Record> r) {
    auto it = tasks_.find(r->id);
    if (!r->owners && !r->pins && it != tasks_.end() && !it->second.callbacks &&
        !it->second.service_pending && !it->second.return_pending) {
        r->output.reset();
        if (r->reclaimed)
            return;
        r->reclaimed = true;
        // Run after the current completion/caller stack has returned.
        if (!bridge_.dispatching())
            tasks_.erase(r->id);
        else
            bridge_.enqueue(now_(), EventBridge::ContinueSubmit, "collect task", [this, r] {
                if (!r->owners && !r->pins)
                    tasks_.erase(r->id);
            });
    }
}
void Runtime::release(Handle h) {
    auto r = checked(h);
    *h.active_ = false;
    --r->owners;
    collect(r);
}
Bytes Runtime::try_get(Handle h, content::Consumer consumer) const {
    auto r = checked(h);
    auto required = consumer == content::Consumer::ControllerExecutor ? Location::DeviceBuffer
                                                                      : Location::HostMemory;
    if (r->location != required)
        throw content::Error(Code::PermissionDenied,
                             "result location inaccessible",
                             {r->query, r->id, now_()},
                             r->id);
    if (r->failure)
        throw content::Error(r->failure->code,
                             r->failure->detail,
                             {r->query, r->id, now_()},
                             r->id,
                             r->failure->causal_parent);
    if (!r->visible)
        throw content::Error(
            Code::NotReady, "private result not published", {r->query, r->id, now_()}, r->id);
    return *r->output;
}
void Runtime::when_all(std::vector<Handle> inputs, uint64_t id, std::function<void(Outcome)> cb) {
    wait(std::move(inputs), id, std::move(cb));
}
uint64_t Runtime::wait(std::vector<Handle> inputs, uint64_t id, std::function<void(Outcome)> cb) {
    if (!id || !cb)
        throw content::Error(Code::InvalidArgument, "invalid continuation ID");
    if (next_wait_ == UINT64_MAX)
        throw content::Error(Code::Overflow, "wait ID overflow");
    Wait w{id, {}, std::move(cb)};
    for (auto h : inputs)
        w.inputs.push_back(checked(h));
    for (auto& r : w.inputs)
        ++r->pins;
    auto key = next_wait_++;
    waits_.emplace(key, std::move(w));
    scan();
    return key;
}
void Runtime::scan() {
    for (auto it = waits_.begin(); it != waits_.end();) {
        bool ready = true;
        Outcome outcome;
        for (auto& r : it->second.inputs) {
            if (r->failure && !outcome.failure)
                outcome.failure = r->failure;
            if (!r->visible)
                ready = false;
        }
        if (!ready && !outcome.failure) {
            ++it;
            continue;
        }
        auto w = std::move(it->second);
        it = waits_.erase(it);
        bridge_.enqueue(now_(),
                        EventBridge::ContinueSubmit,
                        "when_all=" + std::to_string(w.id),
                        [this, w = std::move(w), outcome]() mutable {
                            for (auto& r : w.inputs) {
                                --r->pins;
                                collect(r);
                            }
                            w.callback(outcome);
                        });
    }
}
void Runtime::start(uint64_t id) {
    auto& t = task(id);
    if (t.state != State::WaitingResource)
        throw content::Error(
            Code::InvalidArgument, "start outside WaitingResource", {t.op.query, id, now_()});
    transition(t, State::Running);
    try {
        std::vector<Bytes> values;
        for (auto h : t.op.inputs) {
            values.push_back(try_get(h, t.op.executor));
        }
        t.result->output = t.op.evaluate(values);
        for (auto h : t.op.inputs)
            release(h);
        t.op.inputs.clear();
        t.op.evaluate = {};
        t.service_pending = true;
        try {
            t.op.service(id);
        } catch (...) {
            t.service_pending = false;
            throw;
        }
        if (t.state == State::Failed) {
            t.service_pending = false;
            collect(t.result);
        }
    } catch (const content::Error& e) {
        fail(id, e.code, e.what());
    } catch (const std::exception& e) {
        fail(id, Code::BackendFailure, e.what());
    }
}
void Runtime::boundary(uint64_t id, State expected, State next) {
    auto& t = task(id);
    if (t.state != expected)
        throw content::Error(
            Code::InvalidArgument, "duplicate/out-of-order completion", {t.op.query, id, now_()});
    transition(t, next);
}
void Runtime::service_done(uint64_t id) {
    auto& current = task(id);
    if (current.state == State::Failed && current.service_pending) {
        current.service_pending = false;
        collect(current.result);
        throw content::Error(
            Code::BackendFailure, "service completed after failure", {0, id, now_()});
    }
    current.service_pending = false;
    boundary(id, State::Running, State::ServiceDone);
    defer(id, EventBridge::Publish, "service task=" + std::to_string(id), [this, id] {
        if (!tasks_.count(id))
            return;
        auto& t = task(id);
        if (t.state == State::Failed)
            return;
        if (t.op.needs_return) {
            transition(t, State::WaitingReturn);
            try {
                t.return_pending = true;
                t.op.send_return(id, t.result->output->size());
                if (t.state == State::Failed) {
                    t.return_pending = false;
                    collect(t.result);
                }
            } catch (const std::exception& e) {
                t.return_pending = false;
                fail(id, Code::BackendFailure, e.what());
            }
        } else
            publish(id);
    });
}
void Runtime::return_done(uint64_t id) {
    auto& t = task(id);
    if (t.state == State::Failed && t.return_pending) {
        t.return_pending = false;
        collect(t.result);
        throw content::Error(
            Code::BackendFailure, "return completed after failure", {0, id, now_()});
    }
    if (t.state != State::WaitingReturn || t.return_arrived)
        throw content::Error(
            Code::InvalidArgument, "duplicate/out-of-order return", {t.op.query, id, now_()});
    t.return_pending = false;
    t.return_arrived = true;
    defer(id, EventBridge::Publish, "return task=" + std::to_string(id), [this, id] {
        if (tasks_.count(id) && task(id).state != State::Failed)
            publish(id);
    });
}
void Runtime::publish(uint64_t id) {
    auto& t = task(id);
    transition(t, State::Visible);
    t.result->visible = true;
    --t.result->pins;
    scan();
    collect(t.result);
}
void Runtime::failed(uint64_t id, Failure root) {
    auto& t = task(id);
    if (t.state == State::Visible || t.state == State::Failed)
        throw content::Error(Code::InvalidArgument, "terminal task failure");
    if (auto it = waits_.find(t.wait); it != waits_.end()) {
        for (auto& r : it->second.inputs) {
            --r->pins;
            collect(r);
        }
        waits_.erase(it);
        --t.callbacks;
    }
    t.wait = 0;
    transition(t, State::Failed);
    t.result->failure = root;
    t.result->output.reset();
    --t.result->pins;
    for (auto h : t.op.inputs)
        release(h);
    t.op.inputs.clear();
    t.op.evaluate = {};
    scan();
    collect(t.result);
}
void Runtime::fail(uint64_t id, Code code, std::string detail) {
    auto& t = task(id);
    if (code == Code::NotReady)
        throw content::Error(Code::InvalidArgument, "NotReady is not a failure");
    failed(id, {code, t.op.query, id, id, now_(), id, std::move(detail)});
}
void Runtime::fail_after_drain(uint64_t id, Code code, std::string detail) {
    auto& t = task(id);
    t.service_pending = t.return_pending = false;
    fail(id, code, std::move(detail));
}
State Runtime::state(uint64_t id) const {
    return tasks_.at(id).state;
}
size_t Runtime::live_results() const {
    size_t n = 0;
    for (auto& p : tasks_)
        n += !p.second.result->reclaimed;
    return n;
}
size_t Runtime::private_slots() const {
    size_t n = 0;
    for (auto& p : tasks_)
        n += p.second.result->output.has_value() && !p.second.result->visible;
    return n;
}
std::string Runtime::diagnostic() const {
    std::ostringstream out;
    for (auto& p : tasks_) {
        auto& t = p.second;
        if (t.state == State::Visible || t.state == State::Failed)
            continue;
        out << " query=" << t.op.query << " task=" << p.first << " state=" << name(t.state)
            << " owners=" << t.result->owners << " pins=" << t.result->pins << " dependencies=[";
        for (auto h : t.op.inputs)
            out << h.id() << ":" << name(tasks_.at(h.id()).state) << ",";
        out << "] resource=" << (t.state == State::WaitingResource ? "awaiting grant" : "adapter")
            << ";";
    }
    for (auto& p : waits_)
        out << " continuation=" << p.first << " pending_inputs=" << p.second.inputs.size();
    return out.str();
}
void Runtime::require_progress() const {
    size_t n = waits_.size();
    for (auto& p : tasks_)
        n += p.second.state != State::Visible && p.second.state != State::Failed;
    bridge_.require_progress(n, diagnostic());
}
} // namespace ndpsim::runtime
