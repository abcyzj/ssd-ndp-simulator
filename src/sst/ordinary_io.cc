#include <sst/core/sst_config.h>
#include <sst/core/component.h>
#include <sst/core/link.h>
#include "backend/event_bridge.h"
#include "ordinary_fixture.h"
#include "backend/observation.h"
namespace ndpsim {
class IOWake: public SST::Event {public: uint64_t generation; explicit IOWake(uint64_t g):generation(g){} };
class OrdinaryIO: public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(OrdinaryIO,"ndpsim","OrdinaryIO",SST_ELI_ELEMENT_VERSION(0,1,0),"Ordinary NVMe validation",COMPONENT_CATEGORY_UNCATEGORIZED)
    SST_ELI_DOCUMENT_PARAMS({"observe","Passive research observation","0"},{"miss","Small CMT","0"},{"qd","Concurrent submissions","1"},
        {"single_read","Isolated one-sector NAND experiment","0"},{"read_delay","Additional NAND read ns (acceptance only)","0"})
    OrdinaryIO(SST::ComponentId_t id,SST::Params& p):SST::Component(id) {
        fixture=std::make_unique<OrdinaryFixture>(p.find<int>("miss",0),p.find<unsigned>("qd",1),p.find<int>("single_read",0),p.find<uint64_t>("read_delay",0));
        if(p.find<int>("observe",0)) {
            observation=std::make_unique<Observation>();
            fixture->flow->submitted=[this](uint64_t id,uint64_t lba,unsigned n){if(fixture->measuring) observation->request("request_submit",id,Simulator->Time(),lba,n);};
            auto complete=fixture->flow->completed;
            fixture->flow->completed=[this,complete](ExternalFlow::Completion c){complete(c);if(fixture->measuring){last_completion=c.time;observation->request("request_complete",c.token,c.time,c.lba,c.sectors);}};
        }
        link=configureSelfLink("wake","1ns",new SST::Event::Handler<OrdinaryIO,&OrdinaryIO::wake>(this));
        bridge=std::make_unique<EventBridge>([this]{return getCurrentSimTimeNano();},[this](uint64_t d,uint64_t g){++pending;link->send(d,new IOWake(g));});
        registerAsPrimaryComponent();primaryComponentDoNotEndSim();
    }
    void setup() override {fixture->start();bridge->start();}
    void finish() override {
        fixture->verify();
        if(observation) observation->report(fixture->origin,last_completion);
        observation.reset();
        bridge.reset();fixture.reset();std::puts("M1.3 PASS SST");
    }
private:
    void wake(SST::Event* raw) {
        std::unique_ptr<IOWake> e(static_cast<IOWake*>(raw));--pending;
        bridge->wake(e->generation);fixture->poll();
        bridge->require_progress(fixture->target-fixture->done,"ordinary IO");
        if(!pending && bridge->idle()) primaryComponentOKToEndSim();
    }
    std::unique_ptr<Observation> observation; uint64_t last_completion=0;
    unsigned pending=0;SST::Link* link;
    std::unique_ptr<OrdinaryFixture> fixture;std::unique_ptr<EventBridge> bridge;
};
}
