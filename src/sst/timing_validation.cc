#include <sst/core/sst_config.h>
#include <sst/core/component.h>
#include <sst/core/link.h>
#include "timing/runtime.h"

namespace ndpsim::timing {
class TimingWake : public SST::Event {
public:
    explicit TimingWake(uint64_t g) : generation(g) {}
    uint64_t generation;
};
// P07 integration fixture, not the P08 DAG loader or P11 user execution entry.
class TimingValidation : public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(TimingValidation, "ndpsim_timing_validation", "Runtime",
                              SST_ELI_ELEMENT_VERSION(0, 4, 5),
                              "Metadata runtime SST hand timing validation", COMPONENT_CATEGORY_UNCATEGORIZED)
    SST_ELI_DOCUMENT_PARAMS({"input_ns", "actual input delay", "3"},
                           {"service_ns", "explicit compute demand", "5"},
                           {"return_ns", "actual return delay", "7"})
    TimingValidation(SST::ComponentId_t id, SST::Params& p) : SST::Component(id) {
        if (getNumRanks().rank != 1 || getNumRanks().thread != 1)
            throw std::logic_error("timing runtime requires single rank/thread");
        input_ = p.find<uint64_t>("input_ns", 3);
        service_ = p.find<uint64_t>("service_ns", 5);
        return_ = p.find<uint64_t>("return_ns", 7);
        expected_ = checked_add(checked_add(input_, service_), return_);
        link_ = configureSelfLink("wake", "1ns",
            new SST::Event::Handler<TimingValidation, &TimingValidation::wake>(this));
        auto now = [this] { return getCurrentSimTimeNano(); };
        bridge_ = std::make_unique<EventBridge>(now, [this](auto delay, auto generation) {
            ++pending_; link_->send(delay, new TimingWake(generation));
        }, 100000, EventBridge::BackendMode::PlatformOnly);
        runtime_ = std::make_unique<Runtime>(*bridge_, now);
        cpu_ = std::make_unique<HostCpu>(*bridge_, now, 1);
        registerAsPrimaryComponent(); primaryComponentDoNotEndSim();
    }
    void setup() override {
        bridge_->start();
        Operation o;
        o.key = {0, "metadata", "compute"}; o.resource = cpu_.get(); o.duration_ns = service_;
        o.outputs = {{"result", "opaque", UINT64_MAX, 0, ""}};
        o.input = delay(input_); o.send_return = delay(return_);
        auto source = runtime_->submit(o);
        o.key.op = "successor"; o.inputs = {source}; o.duration_ns = 0;
        o.input = {}; o.send_return = {};
        auto child = runtime_->submit(o);
        runtime_->when_complete(child, [this, source, child](Outcome x) {
            if (x.failure || getCurrentSimTimeNano() != expected_ ||
                runtime_->describe(source).at(0).logical_bytes != UINT64_MAX)
                throw std::logic_error("SST metadata timing mismatch");
            done_ = true;
            runtime_->release(source); runtime_->release(child);
        });
    }
    void finish() override {
        if (!done_ || runtime_->task_records() || runtime_->pins() || cpu_->pending() ||
            cpu_->records().size() != 2 || cpu_->records()[1].start != expected_)
            throw std::logic_error("SST metadata drain mismatch");
        std::puts("P07 SST PASS metadata timing and zero terminal resources");
    }
private:
    Adapter delay(uint64_t ns) {
        return [this, ns](Done done) {
            bridge_->enqueue(checked_add(getCurrentSimTimeNano(), ns), EventBridge::Backend,
                             "actual SST adapter arrival", [done] { done({}); });
        };
    }
    void wake(SST::Event* raw) {
        std::unique_ptr<TimingWake> e(static_cast<TimingWake*>(raw)); --pending_;
        bridge_->wake(e->generation); runtime_->require_progress();
        if (!pending_ && bridge_->idle()) {
            if (!done_) throw std::logic_error("premature SST termination");
            primaryComponentOKToEndSim();
        }
    }
    SST::Link* link_;
    uint64_t input_, service_, return_, expected_;
    size_t pending_ = 0;
    bool done_ = false;
    std::unique_ptr<EventBridge> bridge_;
    std::unique_ptr<Runtime> runtime_;
    std::unique_ptr<HostCpu> cpu_;
};
} // namespace ndpsim::timing
