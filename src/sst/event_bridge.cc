#include <sst/core/sst_config.h>
#include <sst/core/component.h>
#include <sst/core/link.h>
#include "backend/event_bridge.h"
#include "integer_requests.h"
#include <memory>
namespace ndpsim {
class BridgeWake: public SST::Event { public: explicit BridgeWake(uint64_t g):generation(g){} uint64_t generation; };
class BridgeValidation: public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(BridgeValidation,"ndpsim","EventBridge",SST_ELI_ELEMENT_VERSION(0,1,0),
        "Formal event bridge controlled validation",COMPONENT_CATEGORY_UNCATEGORIZED)
    BridgeValidation(SST::ComponentId_t id,SST::Params&):SST::Component(id) {
        if(getNumRanks().rank!=1 || getNumRanks().thread!=1) throw std::logic_error("bridge requires single rank/thread");
        link_=configureSelfLink("wake","1ns",new SST::Event::Handler<BridgeValidation,&BridgeValidation::wake>(this));
        bridge_=std::make_unique<EventBridge>([this]{return getCurrentSimTimeNano();},[this](uint64_t delay,uint64_t g){++pending_;link_->send(delay,new BridgeWake(g));});
        registerAsPrimaryComponent(); primaryComponentDoNotEndSim();
    }
    void setup() override {
        Simulator->Reset(); Simulator->AddObject(&requests_); Simulator->Initialize_simulation(); bridge_->start();
        bridge_->enqueue(10,EventBridge::ContinueSubmit,"continue",[this]{
            order_.push_back(3);
            bridge_->enqueue(10,EventBridge::Publish,"next delta publish",[this]{order_.push_back(4);});
        });
        bridge_->enqueue(10,EventBridge::Publish,"publish",[this]{order_.push_back(2);});
        bridge_->enqueue(10,EventBridge::ResourceCompleteRelease,"release",[this]{order_.push_back(1);});
    }
    void finish() override {
        if(requests_.completed!=4 || order_!=std::vector<int>({1,2,3,4})) getSimulationOutput().fatal(CALL_INFO,1,"bridge validation mismatch\n");
        bridge_.reset(); Simulator->Reset(); std::puts("M1.2 PASS integer requests and phases");
    }
private:
    void wake(SST::Event* raw) {
        std::unique_ptr<BridgeWake> event(static_cast<BridgeWake*>(raw)); --pending_;
        try { bridge_->wake(event->generation); bridge_->require_progress(4-requests_.completed,"controlled requests");
            if(!pending_ && bridge_->idle()) primaryComponentOKToEndSim();
        } catch(const std::exception& e) { getSimulationOutput().fatal(CALL_INFO,1,"%s\n",e.what()); }
    }
    SST::Link* link_; std::unique_ptr<EventBridge> bridge_; IntegerRequests requests_; unsigned pending_=0;
    std::vector<int> order_;
};
}
