#include <sst/core/sst_config.h>
#include <sst/core/component.h>
#include <sst/core/link.h>
#include "controller_scenario.h"
namespace ndpsim {
class ControllerWake : public SST::Event {
public:
    explicit ControllerWake(uint64_t g) : generation(g) {
    }
    uint64_t generation;
};
class ControllerValidation : public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(ControllerValidation,
                               "ndpsim",
                               "ControllerValidation",
                               SST_ELI_ELEMENT_VERSION(0, 1, 0),
                               "Controller buffer validation",
                               COMPONENT_CATEGORY_UNCATEGORIZED)
    ControllerValidation(SST::ComponentId_t id, SST::Params& params) : SST::Component(id) {
        if (getNumRanks().rank != 1 || getNumRanks().thread != 1)
            throw std::logic_error("PCIe requires one rank/thread");
        link_ = configureSelfLink(
            "wake",
            "1ns",
            new SST::Event::Handler<ControllerValidation, &ControllerValidation::wake>(this));
        auto now = [this] {
            return getCurrentSimTimeNano();
        };
        bridge_ = std::make_unique<EventBridge>(now, [this](auto d, auto g) {
            ++pending_;
            link_->send(d, new ControllerWake(g));
        });
        scenario_ = std::make_unique<test::ControllerScenario>(
            *bridge_, now, params.find<std::string>("mode", ""));
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
        std::unique_ptr<ControllerWake> e(static_cast<ControllerWake*>(raw));
        --pending_;
        try {
            bridge_->wake(e->generation);
            scenario_->poll();
            bridge_->require_progress(scenario_->finished ? 0 : 1, "Controller validation");
            if (!pending_ && bridge_->idle())
                primaryComponentOKToEndSim();
        } catch (const std::exception& ex) {
            getSimulationOutput().fatal(CALL_INFO, 1, "%s\n", ex.what());
        }
    }
    SST::Link* link_;
    unsigned pending_ = 0;
    std::unique_ptr<EventBridge> bridge_;
    std::unique_ptr<test::ControllerScenario> scenario_;
};
} // namespace ndpsim
