#include <sst/core/sst_config.h>
#include <sst/core/component.h>
#include <sst/core/link.h>
#include "controller_timing_scenario.h"
namespace ndpsim::test::p12 {
class Wake : public SST::Event {
public:
    explicit Wake(uint64_t g) : generation(g) {
    }
    uint64_t generation;
};
class ControllerValidation : public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(ControllerValidation,
                               "ndpsim_controller_validation",
                               "Controller",
                               SST_ELI_ELEMENT_VERSION(0, 4, 5),
                               "P12 size-only Controller validation",
                               COMPONENT_CATEGORY_UNCATEGORIZED)
    ControllerValidation(SST::ComponentId_t id, SST::Params& params) : SST::Component(id) {
        if (getNumRanks().rank != 1 || getNumRanks().thread != 1)
            throw std::logic_error("single rank/thread");
        link_ = configureSelfLink(
            "wake",
            "1ns",
            new SST::Event::Handler<ControllerValidation, &ControllerValidation::wake>(this));
        auto now = [this] {
            return getCurrentSimTimeNano();
        };
        bridge_ = std::make_unique<EventBridge>(now, [this](auto d, auto g) {
            ++pending_;
            link_->send(d, new Wake(g));
        });
        scenario_ = std::make_unique<Scenario>(*bridge_, now, params.find<std::string>("mode", ""));
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
        std::unique_ptr<Wake> w(static_cast<Wake*>(raw));
        --pending_;
        try {
            bridge_->wake(w->generation);
            scenario_->poll();
            bridge_->require_progress(scenario_->finished ? 0 : 1, "P12 Controller");
            if (!pending_ && bridge_->idle())
                primaryComponentOKToEndSim();
        } catch (const std::exception& e) {
            getSimulationOutput().fatal(CALL_INFO, 1, "%s\n", e.what());
        }
    }
    SST::Link* link_;
    uint64_t pending_ = 0;
    std::unique_ptr<EventBridge> bridge_;
    std::unique_ptr<Scenario> scenario_;
};
} // namespace ndpsim::test::p12
