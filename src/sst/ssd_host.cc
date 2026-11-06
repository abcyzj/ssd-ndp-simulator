#include "extensions/graph/ssd.h"
#include <sst/core/component.h>
#include <sst/core/link.h>
namespace ndpsim::graph {
class Wake : public SST::Event {
public:
    explicit Wake(uint64_t g) : generation(g) {
    }
    uint64_t generation;
};
class SsdHost : public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(SsdHost,
                               "ndpsim_graph_ssd",
                               "Host",
                               SST_ELI_ELEMENT_VERSION(0, 4, 5),
                               "Graph timing-only SSD",
                               COMPONENT_CATEGORY_UNCATEGORIZED)
    SST_ELI_DOCUMENT_PARAMS({"config", "ndpsim-graph-ssd-run/1 path", ""})
    SsdHost(SST::ComponentId_t id, SST::Params& p) : SST::Component(id) {
        if (getNumRanks().rank != 1 || getNumRanks().thread != 1)
            throw std::runtime_error("single rank/thread required");
        link_ = configureSelfLink(
            "wake", "1ns", new SST::Event::Handler<SsdHost, &SsdHost::wake>(this));
        auto now = [this] {
            return getCurrentSimTimeNano();
        };
        bridge_ = std::make_unique<EventBridge>(now, [this](auto d, auto g) {
            ++pending_;
            link_->send(d, new Wake(g));
        });
        run_ = std::make_unique<SsdRun>(p.find<std::string>("config"), *bridge_, now);
        registerAsPrimaryComponent();
        primaryComponentDoNotEndSim();
    }
    void setup() override {
        run_->start();
    }
    void finish() override {
        run_->verify();
        if (!run_->success())
            throw std::runtime_error("SSD execution failed");
        run_.reset();
        bridge_.reset();
    }

private:
    SST::Link* link_;
    uint64_t pending_ = 0;
    std::unique_ptr<EventBridge> bridge_;
    std::unique_ptr<SsdRun> run_;
    void wake(SST::Event* raw) {
        std::unique_ptr<Wake> w(static_cast<Wake*>(raw));
        --pending_;
        try {
            bridge_->wake(w->generation);
            run_->poll();
            bridge_->require_progress(run_->finished() ? 0 : 1, "graph SSD");
            if (!pending_ && bridge_->idle())
                primaryComponentOKToEndSim();
        } catch (const std::exception& e) {
            getSimulationOutput().fatal(CALL_INFO, 1, "%s\n", e.what());
        }
    }
};
} // namespace ndpsim::graph
