#include "run.h"
#include <sst/core/component.h>
#include <sst/core/link.h>

namespace maxsim {
class Wake : public SST::Event {
public:
    explicit Wake(uint64_t value) : generation(value) {}
    uint64_t generation;
};

class Host : public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(Host, "ndpsim_maxsim", "Host",
                               SST_ELI_ELEMENT_VERSION(0, 4, 5),
                               "MaxSim E0 timing-only SSD execution",
                               COMPONENT_CATEGORY_UNCATEGORIZED)
    SST_ELI_DOCUMENT_PARAMS({"config", "ndpsim-maxsim-run/1 path", ""})

    Host(SST::ComponentId_t id, SST::Params& params) : SST::Component(id) {
        if (getNumRanks().rank != 1 || getNumRanks().thread != 1)
            throw std::runtime_error("MaxSim requires one rank and one thread");
        link_ = configureSelfLink("wake", "1ns", new SST::Event::Handler<Host, &Host::wake>(this));
        auto now = [this] { return getCurrentSimTimeNano(); };
        bridge_ = std::make_unique<ndpsim::EventBridge>(now, [this](auto delay, auto generation) {
            ++pending_;
            link_->send(delay, new Wake(generation));
        });
        try {
            run_ = std::make_unique<Run>(params.find<std::string>("config"), *bridge_, now);
        } catch (const std::exception& e) {
            getSimulationOutput().fatal(CALL_INFO, 1, "%s\n", e.what());
        }
        registerAsPrimaryComponent();
        primaryComponentDoNotEndSim();
    }

    void setup() override {
        run_->start();
        if (run_->finished())
            primaryComponentOKToEndSim();
    }
    void finish() override { run_->verify(); }

private:
    SST::Link* link_;
    uint64_t pending_ = 0;
    std::unique_ptr<ndpsim::EventBridge> bridge_;
    std::unique_ptr<Run> run_;

    void wake(SST::Event* raw) {
        std::unique_ptr<Wake> event(static_cast<Wake*>(raw));
        --pending_;
        try {
            if (bridge_->wake(event->generation))
                run_->poll();
            bridge_->require_progress(run_->finished() ? 0 : 1, "MaxSim E0");
            if (run_->finished() && !pending_ && bridge_->idle())
                primaryComponentOKToEndSim();
        } catch (const std::exception& e) {
            getSimulationOutput().fatal(CALL_INFO, 1, "%s\n", e.what());
        }
    }
};
}
