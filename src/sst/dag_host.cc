#include "sst/dag_host.h"
namespace ndpsim::dag {
namespace {
class Wake : public SST::Event {
  public:
    explicit Wake(uint64_t g) : generation(g) {}
    uint64_t generation;
};
} // namespace
struct GraphHost::Data {
    SST::Link *link = nullptr;
    std::unique_ptr<Package> package;
    std::unique_ptr<EventBridge> bridge;
    std::unique_ptr<timing::Runtime> runtime;
    std::unique_ptr<Executor> executor;
    bool done = false;
    uint64_t drained = 0;
};
GraphHost::GraphHost(SST::ComponentId_t id, HostInputs inputs)
    : SST::Component(id), data_(std::make_unique<Data>()) {
    if (getNumRanks().rank != 1 || getNumRanks().thread != 1)
        throw std::runtime_error("DAG requires single rank/thread");
    auto &d = *data_;
    d.package =
        std::make_unique<Package>(inputs.package, inputs.receipt, inputs.scratch, inputs.limits);
    d.link = configureSelfLink("dag-wake", "1ns",
                               new SST::Event::Handler<GraphHost, &GraphHost::wake>(this));
    auto clock = [this] { return getCurrentSimTimeNano(); };
    d.bridge = std::make_unique<EventBridge>(
        clock,
        [this](auto delay, auto generation) { data_->link->send(delay, new Wake(generation)); },
        1000000, EventBridge::BackendMode::PlatformOnly);
    d.runtime = std::make_unique<timing::Runtime>(*d.bridge, clock);
    d.runtime->observations().enabled = false;
    d.executor = std::make_unique<Executor>(*d.package, *d.runtime, *d.bridge, clock,
                                            std::move(inputs.config),
                                            [this](const Stage &s) { return lower(s); });
    d.executor->requests().sink = [this](const RequestRecord &r) { on_request(r); };
    d.executor->stages().sink = [this](const StageRecord &s) { on_stage(s); };
    registerAsPrimaryComponent();
    primaryComponentDoNotEndSim();
}
GraphHost::~GraphHost() = default;
void GraphHost::setup() {
    auto &d = *data_;
    d.bridge->start();
    d.executor->start([this](timing::Outcome o) {
        data_->done = true;
        data_->drained = getCurrentSimTimeNano();
        on_drained(o);
    });
}
void GraphHost::wake(SST::Event *raw) {
    std::unique_ptr<Wake> event(static_cast<Wake *>(raw));
    auto &d = *data_;
    try {
        d.bridge->wake(event->generation);
        d.executor->require_progress();
        if (d.done && d.bridge->idle())
            primaryComponentOKToEndSim();
    } catch (const std::exception &e) {
        getSimulationOutput().fatal(CALL_INFO, 1, "%s\n", e.what());
    }
}
void GraphHost::finish() {
    auto &d = *data_;
    if (!d.done || !d.executor->drained() || d.runtime->task_records() || d.runtime->pins())
        throw std::runtime_error("DAG SST finish before drain");
}
EventBridge &GraphHost::bridge() { return *data_->bridge; }
timing::Runtime &GraphHost::runtime() { return *data_->runtime; }
const Executor &GraphHost::executor() const { return *data_->executor; }
uint64_t GraphHost::drained_time() const { return data_->drained; }
} // namespace ndpsim::dag
