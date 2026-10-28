#include <sst/core/sst_config.h>
#include <sst/core/component.h>
#include <sst/core/link.h>
#include "runtime/runtime.h"
#include <memory>
namespace ndpsim {
class RuntimeWake: public SST::Event {public: explicit RuntimeWake(uint64_t g):generation(g){} uint64_t generation;};
class RuntimeValidation: public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(RuntimeValidation,"ndpsim","RuntimeValidation",SST_ELI_ELEMENT_VERSION(0,1,0),
        "Asynchronous visibility validation",COMPONENT_CATEGORY_UNCATEGORIZED)
    SST_ELI_DOCUMENT_PARAMS({"read_delay","additional input ns","0"},{"compute_delay","additional compute ns","0"},{"return_delay","additional return ns","0"})
    RuntimeValidation(SST::ComponentId_t id,SST::Params& p):SST::Component(id) {
        read_delay_=p.find<uint64_t>("read_delay",0);compute_delay_=p.find<uint64_t>("compute_delay",0);return_delay_=p.find<uint64_t>("return_delay",0);
        if(getNumRanks().rank!=1 || getNumRanks().thread!=1)throw std::logic_error("runtime requires one rank/thread");
        link_=configureSelfLink("wake","1ns",new SST::Event::Handler<RuntimeValidation,&RuntimeValidation::wake>(this));
        bridge_=std::make_unique<EventBridge>([this]{return getCurrentSimTimeNano();},[this](auto d,auto g){++pending_;link_->send(d,new RuntimeWake(g));});
        runtime_=std::make_unique<runtime::Runtime>(*bridge_,[this]{return getCurrentSimTimeNano();});
        registerAsPrimaryComponent();primaryComponentDoNotEndSim();
    }
    void setup() override {
        Simulator->Reset();Simulator->Initialize_simulation();bridge_->start();
        input_=runtime_->submit(operation(1,read_delay_,0));
        auto first=operation(1,20+compute_delay_,30+return_delay_);first.inputs={input_};first.evaluate=[](const auto&){return content::Bytes{42};};
        slow_=runtime_->submit(std::move(first));
        auto successor=operation(1,5,0);successor.inputs={slow_};
        successor.evaluate=[](const auto& v){if(v.at(0)!=content::Bytes{42})throw std::logic_error("wrong real value");return content::Bytes{43};};
        successor_=runtime_->submit(std::move(successor));
        independent_=runtime_->submit(operation(2,7,0));
        runtime_->when_all({independent_},1,[this](runtime::Outcome o){if(o.failure || getCurrentSimTimeNano()!=7)throw std::logic_error("independent query blocked");++resumed_;});
        runtime_->when_all({successor_},2,[this](runtime::Outcome o){if(o.failure || getCurrentSimTimeNano()!=55+read_delay_+compute_delay_+return_delay_ || runtime_->try_get(successor_)!=content::Bytes{43})throw std::logic_error("successor visibility");++resumed_;});
        for(uint64_t t:{1+read_delay_,19+read_delay_+compute_delay_,20+read_delay_+compute_delay_,49+read_delay_+compute_delay_+return_delay_})bridge_->enqueue(t,EventBridge::ContinueSubmit,"early probe",[this]{
            try {runtime_->try_get(slow_);throw std::logic_error("early visibility");}
            catch(const content::Error& e){if(e.code!=content::Code::NotReady)throw;}
            if(runtime_->private_slots()<1)throw std::logic_error("private slot missing");++probes_;
        });
    }
    void finish() override {
        if(resumed_!=2 || probes_!=4)throw std::logic_error("runtime incomplete");
        for(auto h:{input_,slow_,successor_,independent_})runtime_->release(h);
        if(runtime_->live_results() || runtime_->private_slots())throw std::logic_error("runtime leaked results");
        runtime_.reset();bridge_.reset();Simulator->Reset();
        std::printf("M2.2 SST PASS probes=4 independent=7 successor=%llu live=0\n",55+read_delay_+compute_delay_+return_delay_);
    }
private:
    runtime::Operation operation(uint64_t query,uint64_t service,uint64_t ret) {
        runtime::Operation o;o.query=query;o.needs_return=ret!=0;
        o.grant=[this](auto id){runtime_->start(id);};
        o.evaluate=[](const auto&){return content::Bytes{1};};
        o.service=[this,service](auto id){bridge_->enqueue(getCurrentSimTimeNano()+service,EventBridge::ResourceCompleteRelease,"service",[this,id]{runtime_->service_done(id);});};
        o.send_return=[this,ret](auto id,size_t){bridge_->enqueue(getCurrentSimTimeNano()+ret,EventBridge::ResourceCompleteRelease,"return",[this,id]{runtime_->return_done(id);});};return o;
    }
    void wake(SST::Event* raw) {
        std::unique_ptr<RuntimeWake> e(static_cast<RuntimeWake*>(raw));--pending_;
        try {bridge_->wake(e->generation);runtime_->require_progress();if(!pending_&&bridge_->idle())primaryComponentOKToEndSim();}
        catch(const std::exception& ex){getSimulationOutput().fatal(CALL_INFO,1,"%s\n",ex.what());}
    }
    SST::Link* link_;unsigned pending_=0,resumed_=0,probes_=0;
    std::unique_ptr<EventBridge> bridge_;std::unique_ptr<runtime::Runtime> runtime_;
    uint64_t read_delay_=0,compute_delay_=0,return_delay_=0;
    runtime::Handle input_,slow_,successor_,independent_;
};
}
