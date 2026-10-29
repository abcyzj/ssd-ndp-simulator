#include <sst/core/sst_config.h>
#include <sst/core/component.h>
#include <sst/core/link.h>
#include "host_scenario.h"
namespace ndpsim {
class HostWake : public SST::Event {
public:
    explicit HostWake(uint64_t g) : generation(g) {
    }
    uint64_t generation;
};
class HostValidation : public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(HostValidation,
                               "ndpsim",
                               "HostValidation",
                               SST_ELI_ELEMENT_VERSION(0, 1, 0),
                               "Host operator cost validation",
                               COMPONENT_CATEGORY_UNCATEGORIZED)
    HostValidation(SST::ComponentId_t id, SST::Params&) : SST::Component(id) {
        if (getNumRanks().rank != 1 || getNumRanks().thread != 1)
            throw std::logic_error("host requires one rank/thread");
        link_ = configureSelfLink(
            "wake", "1ns", new SST::Event::Handler<HostValidation, &HostValidation::wake>(this));
        auto now = [this] {
            return getCurrentSimTimeNano();
        };
        bridge_ = std::make_unique<EventBridge>(now, [this](auto d, auto g) {
            ++pending_;
            link_->send(d, new HostWake(g));
        });
        runtime_ = std::make_unique<runtime::Runtime>(*bridge_, now);
        model::HostConfig c;
        c.distance = {3, 4};
        c.control = {1, 1};
        cpu_ = std::make_unique<model::HostCpu>(*runtime_, *bridge_, now, c);
        registerAsPrimaryComponent();
        primaryComponentDoNotEndSim();
    }
    void setup() override {
        Simulator->Reset();
        Simulator->Initialize_simulation();
        bridge_->start();
        test::host_scenario(
            *runtime_,
            *cpu_,
            *bridge_,
            [this] {
                return getCurrentSimTimeNano();
            },
            done_);
    }
    void finish() override {
        if (done_ != 1 || cpu_->busy() || runtime_->live_results())
            throw std::logic_error("host incomplete");
        cpu_.reset();
        runtime_.reset();
        bridge_.reset();
        Simulator->Reset();
        std::puts("M2.3 SST PASS input=5 distance=11 control=13 live=0");
    }

private:
    void wake(SST::Event* raw) {
        std::unique_ptr<HostWake> e(static_cast<HostWake*>(raw));
        --pending_;
        try {
            bridge_->wake(e->generation);
            runtime_->require_progress();
            if (!pending_ && bridge_->idle())
                primaryComponentOKToEndSim();
        } catch (const std::exception& ex) {
            getSimulationOutput().fatal(CALL_INFO, 1, "%s\n", ex.what());
        }
    }
    SST::Link* link_;
    unsigned pending_ = 0, done_ = 0;
    std::unique_ptr<EventBridge> bridge_;
    std::unique_ptr<runtime::Runtime> runtime_;
    std::unique_ptr<model::HostCpu> cpu_;
};
} // namespace ndpsim
