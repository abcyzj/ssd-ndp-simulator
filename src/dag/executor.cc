#include "dag/executor.h"
#include <algorithm>
#include <numeric>
#include <set>

namespace ndpsim::dag {
namespace {
void need(bool b, const char *m) {
    if (!b)
        throw std::runtime_error(std::string("DAG executor: ") + m);
}
bool outputs_equal(const std::vector<capture::Output> &a, const std::vector<capture::Output> &b) {
    if (a.size() != b.size())
        return false;
    for (size_t i = 0; i < a.size(); ++i)
        if (a[i].port != b[i].port || a[i].type != b[i].type ||
            a[i].logical_bytes != b[i].logical_bytes || a[i].version != b[i].version ||
            a[i].digest != b[i].digest)
            return false;
    return true;
}
} // namespace
struct Executor::Data {
    struct Request {
        size_t index, serial;
        Graph graph;
        std::vector<uint64_t> incoming;
        std::deque<size_t> ready;
        std::vector<std::optional<timing::CompletionToken>> tokens;
        RequestRecord record;
        uint64_t outstanding = 0, completed = 0;
        bool worker = false, cleanup = false;
    };
    Package &package;
    timing::Runtime &runtime;
    EventBridge &bridge;
    EventBridge::Clock now;
    Config config;
    Lower lower;
    Metrics metrics;
    std::vector<size_t> order;
    std::map<size_t, std::shared_ptr<Request>> active;
    History<RequestRecord> requests;
    History<StageRecord> stages;
    timing::Done done;
    std::optional<timing::Failure> failure;
    size_t next = 0;
    bool started = false, finished = false, pump_queued = false;
    std::optional<uint64_t> offered_wake;
    Data(Package &p, timing::Runtime &r, EventBridge &b, EventBridge::Clock n, Config c, Lower l)
        : package(p), runtime(r), bridge(b), now(std::move(n)), config(std::move(c)),
          lower(std::move(l)) {}
    void schedule() {
        if (pump_queued || finished)
            return;
        pump_queued = true;
        bridge.enqueue(now(), EventBridge::ContinueSubmit, "DAG ready/admit", [this] {
            pump_queued = false;
            pump();
        });
    }
    void abort(timing::Failure f) {
        if (failure)
            return;
        failure = std::move(f);
        metrics.skipped = order.size() - next;
        next = order.size();
        if (offered_wake) {
            bridge.cancel(*offered_wake);
            offered_wake.reset();
        }
        for (auto &entry : active) {
            auto q = entry.second;
            for (auto &token : q->tokens)
                if (token) {
                    auto s = runtime.state(*token);
                    if (s != timing::State::Visible && s != timing::State::Failed &&
                        s != timing::State::FailedDraining)
                        runtime.fail(*token, failure->code, failure->detail);
                }
            if (!q->outstanding)
                cleanup(q);
        }
        schedule();
    }
    void submit(const std::shared_ptr<Request> &q, size_t i) {
        // Called only after every source completion callback (phase3 after Visible).
        try {
            auto stage = package.stage(q->graph.nodes[i]);
            auto op = lower(stage);
            need(op.key.query == stage.key.query && op.key.op == stage.key.op &&
                     op.key.stage == stage.key.stage,
                 "lowering changed stable key");
            need(op.inputs.empty() && op.work == stage.metadata.work &&
                     op.features == stage.metadata.features &&
                     outputs_equal(op.outputs, stage.metadata.outputs),
                 "lowering changed metadata/hidden dependency");
            auto token = runtime.submit(std::move(op));
            q->tokens[i] = token;
            ++q->outstanding;
            ++metrics.outstanding;
            auto submitted = now();
            runtime.when_complete(token, [this, q, i, token, submitted](timing::Outcome result) {
                q->tokens[i].reset();
                runtime.release(token);
                --q->outstanding;
                --metrics.outstanding;
                ++q->completed;
                stages.push_back({q->graph.nodes[i].key, submitted, now(), !result.failure});
                if (result.failure)
                    abort(*result.failure);
                if (!failure) {
                    for (auto successor : q->graph.successors[i]) {
                        need(q->incoming[successor] > 0, "dependency underflow");
                        --q->incoming[successor];
                        --metrics.ready_edges;
                        if (!q->incoming[successor])
                            q->ready.push_back(successor);
                    }
                    if (q->graph.nodes[i].terminal)
                        q->record.visible = std::max(q->record.visible, now());
                }
                if ((failure && !q->outstanding) || q->completed == q->graph.nodes.size())
                    cleanup(q);
                schedule();
            });
        } catch (const std::exception &e) {
            abort({0, "DagSubmissionFailure", e.what()});
        }
    }
    void cleanup(const std::shared_ptr<Request> &q) {
        if (q->cleanup)
            return;
        q->cleanup = true;
        // Runtime's current callback returns and releases its last callback pin
        // before graph/worker/admission ownership is reclaimed here.
        bridge.enqueue(now(), EventBridge::ContinueSubmit, "DAG query drain", [this, q] {
            need(!q->outstanding, "query cleanup before callbacks drained");
            q->record.drained = now();
            q->record.success = !failure;
            if (failure)
                q->record.visible = 0;
            requests.push_back(q->record);
            metrics.active_stages -= q->graph.nodes.size();
            metrics.active_index_bytes -= q->graph.query.index_bytes;
            --metrics.active_queries;
            if (q->worker)
                --metrics.active_workers;
            // All dependency edges die with the graph, including skipped descendants.
            if (failure)
                for (auto n : q->incoming)
                    metrics.ready_edges -= n;
            ++metrics.completed;
            active.erase(q->serial);
            q->tokens.clear();
            q->incoming.clear();
            q->graph = {};
            schedule();
        });
    }
    void pump() {
        if (finished)
            return;
        if (!failure) {
            while (next < order.size() && metrics.active_queries < config.admission) {
                auto index = order[next];
                auto offered = config.offered_ns[index];
                if (offered > now()) {
                    if (!offered_wake)
                        offered_wake = bridge.enqueue(offered, EventBridge::ContinueSubmit,
                                                      "DAG next offered", [this] {
                                                          offered_wake.reset();
                                                          schedule();
                                                      });
                    break;
                }
                try {
                    auto q = std::make_shared<Request>();
                    q->index = index;
                    q->serial = next;
                    q->graph = package.graph(index);
                    auto n = q->graph.nodes.size();
                    q->incoming.resize(n);
                    q->tokens.resize(n);
                    for (size_t i = 0; i < n; ++i) {
                        q->incoming[i] = q->graph.nodes[i].dependencies.size();
                        metrics.ready_edges += q->incoming[i];
                        if (!q->incoming[i])
                            q->ready.push_back(i);
                    }
                    q->record = {q->graph.query.id, offered, now(), 0, 0, 0, false};
                    active.emplace(q->serial, q);
                    ++next;
                    ++metrics.admitted;
                    ++metrics.active_queries;
                    metrics.active_stages += n;
                    metrics.active_index_bytes += q->graph.query.index_bytes;
                    metrics.peak_queries = std::max(metrics.peak_queries, metrics.active_queries);
                    metrics.peak_stages = std::max(metrics.peak_stages, metrics.active_stages);
                    metrics.peak_index_bytes =
                        std::max(metrics.peak_index_bytes, metrics.active_index_bytes);
                } catch (const std::exception &e) {
                    abort({0, "DagAdmissionFailure", e.what()});
                    break;
                }
            }
            // Offered-order FIFO for worker assignment, independent of original IDs.
            for (auto &entry : active) {
                auto q = entry.second;
                if (!q->worker && !q->cleanup && metrics.active_workers < config.workers) {
                    q->worker = true;
                    q->record.worker_start = now();
                    ++metrics.active_workers;
                    metrics.peak_workers = std::max(metrics.peak_workers, metrics.active_workers);
                }
                if (!q->worker || q->cleanup)
                    continue;
                while (!q->ready.empty() && !failure) {
                    auto i = q->ready.front();
                    q->ready.pop_front();
                    submit(q, i);
                }
                if (failure)
                    break;
            }
        }
        finish();
    }
    void finish() {
        if (finished || !started || !active.empty() || next != order.size() || offered_wake ||
            pump_queued)
            return;
        need(!metrics.outstanding && !metrics.ready_edges && !runtime.task_records(),
             "final drain has live resources/tokens");
        finished = true;
        auto callback = std::move(done);
        bridge.enqueue(now(), EventBridge::ContinueSubmit, "DAG experiment drained",
                       [callback, f = failure] { callback({f}); });
    }
};
Executor::Executor(Package &p, timing::Runtime &r, EventBridge &b, EventBridge::Clock n, Config c,
                   Lower lower)
    : data_(std::make_unique<Data>(p, r, b, std::move(n), std::move(c), std::move(lower))) {
    auto &d = *data_;
    need(bool(d.now) && bool(d.lower), "clock/lowering required");
    need(d.config.admission && d.config.workers && d.config.workers <= d.config.admission &&
             d.config.max_active_stages && d.config.max_active_index_bytes,
         "resource configuration");
    if (d.config.offered_ns.empty())
        d.config.offered_ns.resize(p.queries().size(), 0);
    need(d.config.offered_ns.size() == p.queries().size(), "offered query coverage");
    d.order.resize(p.queries().size());
    std::iota(d.order.begin(), d.order.end(), 0);
    std::stable_sort(d.order.begin(), d.order.end(), [&d](size_t a, size_t b) {
        return d.config.offered_ns[a] < d.config.offered_ns[b];
    });
    for (const auto &q : p.queries())
        need(q.stages <= d.config.max_active_stages &&
                 q.index_bytes <= d.config.max_active_index_bytes,
             "single query exceeds active budget");
    // Worst permitted active set, not an implicit fallback to serial admission.
    for (bool bytes : {false, true}) {
        std::vector<uint64_t> demands;
        for (const auto &q : p.queries())
            demands.push_back(bytes ? q.index_bytes : q.stages);
        std::sort(demands.rbegin(), demands.rend());
        uint64_t sum = 0;
        for (size_t i = 0; i < std::min<uint64_t>(demands.size(), d.config.admission); ++i)
            sum = timing::checked_add(sum, demands[i]);
        need(sum <= (bytes ? d.config.max_active_index_bytes : d.config.max_active_stages),
             "admission quota exceeds active budget");
    }
}
Executor::~Executor() = default;
void Executor::start(timing::Done done) {
    auto &d = *data_;
    need(!d.started && bool(done), "duplicate/empty start");
    d.started = true;
    d.done = std::move(done);
    d.schedule();
}
void Executor::require_progress() const {
    auto &d = *data_;
    d.runtime.require_progress();
    d.bridge.require_progress(d.started && !d.finished, "DAG executor unfinished");
}
bool Executor::drained() const { return data_->finished; }
const Metrics &Executor::metrics() const { return data_->metrics; }
History<RequestRecord> &Executor::requests() { return data_->requests; }
History<StageRecord> &Executor::stages() { return data_->stages; }
} // namespace ndpsim::dag
