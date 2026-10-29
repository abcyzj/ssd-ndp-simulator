#include "model/host_cpu.h"
namespace ndpsim::model {
using content::Code;
namespace {
uint64_t add(uint64_t a, uint64_t b) {
    if (b > UINT64_MAX - a)
        throw content::Error(Code::Overflow, "host cost addition");
    return a + b;
}
uint64_t mul(uint64_t a, uint64_t b) {
    if (a && b > UINT64_MAX / a)
        throw content::Error(Code::Overflow, "host cost multiplication");
    return a * b;
}
void validate(const HostConfig& c) {
    if (!c.slots || !c.distance.units_per_ns || !c.control.units_per_ns || c.source.empty())
        throw content::Error(Code::InvalidArgument, "host slots/rates/source required");
}
} // namespace
uint64_t service_ns(const operators::Work& w, Kind k, const HostConfig& c) {
    validate(c);
    const auto& cost = k == Kind::Distance ? c.distance : c.control;
    auto units = k == Kind::Distance ? w.flops
                                     : add(mul(w.candidate_checks, c.check_units),
                                           mul(w.comparisons, c.comparison_units));
    return add(cost.setup_ns, units / cost.units_per_ns + (units % cost.units_per_ns != 0));
}
HostCpu::HostCpu(runtime::Runtime& r, EventBridge& b, EventBridge::Clock n, HostConfig c)
    : runtime_(r), bridge_(b), now_(std::move(n)), config_(std::move(c)) {
    validate(config_);
}
runtime::Handle
HostCpu::submit(uint64_t query, std::vector<runtime::Handle> inputs, Kind kind, Evaluate evaluate) {
    if (!evaluate)
        throw content::Error(Code::InvalidArgument, "host evaluator required");
    auto work = std::make_shared<operators::Work>();
    auto submitted = now_();
    auto ready = std::make_shared<uint64_t>(submitted);
    runtime::Operation op;
    op.query = query;
    op.inputs = std::move(inputs);
    op.grant = [this, submitted, kind, work, ready](auto id) {
        *ready = now_();
        queue_.push_back({id, submitted, kind, work});
        pump();
    };
    op.evaluate = [evaluate = std::move(evaluate), work](const auto& values) {
        auto r = evaluate(values);
        if (r.work.output_bytes != r.bytes.size() || r.work.write_bytes != r.bytes.size())
            throw content::Error(Code::InvalidArgument, "operator output/work mismatch");
        *work = r.work;
        return r.bytes;
    };
    op.service = [this, kind, work, submitted, query, ready](auto id) {
        auto duration = service_ns(*work, kind, config_);
        auto start = now_();
        auto end = add(start, duration);
        services_.push_back({id, submitted, start, end, duration, kind, *work, query, *ready});
        bridge_.enqueue(end,
                        EventBridge::ResourceCompleteRelease,
                        "host service task=" + std::to_string(id),
                        [this, id] {
                            --busy_;
                            if (runtime_.state(id) == runtime::State::Running)
                                runtime_.service_done(id);
                            pump();
                        });
    };
    return runtime_.submit(std::move(op));
}
void HostCpu::pump() {
    while (busy_ < config_.slots && !queue_.empty()) {
        auto job = queue_.front();
        queue_.pop_front();
        if (runtime_.state(job.task) == runtime::State::Failed)
            continue;
        ++busy_;
        runtime_.start(job.task);
        if (runtime_.state(job.task) == runtime::State::Failed)
            --busy_;
    }
}
} // namespace ndpsim::model
