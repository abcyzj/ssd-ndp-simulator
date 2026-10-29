#include <sst/core/sst_config.h>
#include <sst/core/component.h>
#include <sst/core/link.h>
#include "pcie_scenario.h"
namespace ndpsim {
class PcieWake : public SST::Event {
public:
    explicit PcieWake(uint64_t g) : generation(g) {
    }
    uint64_t generation;
};
class PcieValidation : public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(PcieValidation,
                               "ndpsim",
                               "PcieValidation",
                               SST_ELI_ELEMENT_VERSION(0, 1, 0),
                               "Shared PCIe validation",
                               COMPONENT_CATEGORY_UNCATEGORIZED)
    PcieValidation(SST::ComponentId_t id, SST::Params&) : SST::Component(id) {
        if (getNumRanks().rank != 1 || getNumRanks().thread != 1)
            throw std::logic_error("PCIe requires one rank/thread");
        link_ = configureSelfLink(
            "wake", "1ns", new SST::Event::Handler<PcieValidation, &PcieValidation::wake>(this));
        auto now = [this] {
            return getCurrentSimTimeNano();
        };
        bridge_ = std::make_unique<EventBridge>(now, [this](auto d, auto g) {
            ++pending_;
            link_->send(d, new PcieWake(g));
        });
        scenario_ = std::make_unique<test::PcieScenario>(*bridge_, now);
        registerAsPrimaryComponent();
        primaryComponentDoNotEndSim();
    }
    void setup() override {
        scenario_->start();
        bridge_->start();
    }
    void finish() override {
        scenario_->verify();
        scenario_.reset();
        bridge_.reset();
    }

private:
    void wake(SST::Event* raw) {
        std::unique_ptr<PcieWake> e(static_cast<PcieWake*>(raw));
        --pending_;
        try {
            bridge_->wake(e->generation);
            scenario_->poll();
            bridge_->require_progress(scenario_->finished ? 0 : 1, "PCIe validation");
            if (!pending_ && bridge_->idle())
                primaryComponentOKToEndSim();
        } catch (const std::exception& ex) {
            getSimulationOutput().fatal(CALL_INFO, 1, "%s\n", ex.what());
        }
    }
    SST::Link* link_;
    unsigned pending_ = 0;
    std::unique_ptr<EventBridge> bridge_;
    std::unique_ptr<test::PcieScenario> scenario_;
};
} // namespace ndpsim
