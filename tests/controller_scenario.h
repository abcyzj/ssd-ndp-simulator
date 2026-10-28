#pragma once
#include "backend/ordinary_device.h"
#include "model/controller.h"
#include <cstdio>

namespace ndpsim::test {
inline void controller_check(bool ok,const char* detail){if(!ok)throw std::logic_error(detail);}
class ControllerScenario {
public:
    EventBridge& bridge;EventBridge::Clock now;std::string mode;
    OrdinaryDevice device{128};runtime::Runtime runtime;
    std::unique_ptr<content::ContentStore> store;std::unique_ptr<content::Layout> layout;
    std::unique_ptr<content::Handles> handles;std::unique_ptr<LocalReads> reads;
    transport::PcieTransport transport;std::unique_ptr<model::Controller> controller;
    model::ControllerConfig config;std::vector<content::Selection> selections;
    uint64_t origin=0,dma=0,early=0;unsigned completed=0,failed=0,closed=0,ordinary=0,injected=0;
    bool begun=false,finished=false;std::vector<runtime::Handle> results;
    std::map<uint64_t,uint64_t> returned;
    ControllerScenario(EventBridge& b,EventBridge::Clock n,std::string m=""):bridge(b),now(n),mode(std::move(m)),runtime(b,n),transport(*device.link,b,n){
        config.dimension=mode=="cross"?260:2;config.chunk_limit=2;config.max_inflight=4;
        config.vector_sector_bound=mode=="cross"?1536:512;config.scratch_bytes=16;
        config.launch_ns=mode=="compute"?200000:11;config.flops_per_ns=3;config.staging_bytes_per_ns=16;
        auto block=80+2*(config.vector_sector_bound+16);
        config.capacity_bytes=2*config.dimension*4+block;
        if(mode=="compute" || mode=="slots")config.capacity_bytes=16384;
        if(mode=="slots"){config.slots=2;config.launch_ns=200000;}
        if(mode=="zero"){config.launch_ns=0;config.flops_per_ns=UINT64_MAX;config.staging_bytes_per_ns=UINT64_MAX;}
        std::vector<content::Input> inputs;std::vector<content::Placement> placements;
        for(uint32_t i=0;i<6;++i){
            content::ObjectRef ref{1,content::Kind::Vector,i,1};
            inputs.push_back({ref,operators::fp32(std::vector<float>(config.dimension,float(i+1)))});
            auto address=uint64_t(i)*8*512+7*512;
            if(i==5)address+=508; // An intentionally too-large single-vector sector cover.
            placements.push_back({ref,address});
            if(i<5)selections.push_back({ref,{0,config.dimension*4ULL}});
        }
        store=std::make_unique<content::ContentStore>(std::move(inputs));
        layout=std::make_unique<content::Layout>(*store,128,std::move(placements));
        handles=std::make_unique<content::Handles>(*store);
        auto* host=static_cast<SSD_Components::Host_Interface_NVMe*>(device.device->Host_interface);
        reads=std::make_unique<LocalReads>(*host,b,n);
        controller=std::make_unique<model::Controller>(runtime,*handles,*layout,*reads,transport,b,n,config,
            [this](const std::string& stage,content::Context c){
                if(mode==stage && c.query==1 && !injected){++injected;throw content::Error(content::Code::InvalidArgument,"injected "+stage,c);}
            });
        auto invalid=[this](model::ControllerConfig bad,content::Code code){
            bool rejected=false;try{model::Controller candidate(runtime,*handles,*layout,*reads,transport,bridge,now,bad);}
            catch(const content::Error& e){rejected=e.code==code;}controller_check(rejected,"bad startup config accepted");
        };
        auto bad=config;bad.capacity_bytes=4*config.dimension+80+config.vector_sector_bound+16-1;
        invalid(bad,content::Code::CapacityExceeded);
        bad=config;bad.slots=0;invalid(bad,content::Code::InvalidArgument);
        bad=config;bad.staging_bytes_per_ns=0;invalid(bad,content::Code::InvalidArgument);
        bad=config;bad.flops_per_ns=0;invalid(bad,content::Code::InvalidArgument);
        bad=config;bad.chunk_limit=0;invalid(bad,content::Code::InvalidArgument);
        bad=config;bad.max_inflight=0;invalid(bad,content::Code::InvalidArgument);
        bad=config;bad.scratch_bytes=UINT64_MAX;invalid(bad,content::Code::Overflow);
        device.root->data_arrived=[this](auto a,auto bytes){if(begun)dma+=bytes;device.flow->data(a,bytes);};
        device.flow->completed=[this](auto){if(begun)++ordinary;};
    }
    void start(){device.start();}
    void poll(){
        reads->poll();
        if(!begun && device.window->advance(bridge.idle(),now())){
            begun=true;origin=now();dma=0;
            bridge.enqueue(now(),EventBridge::ContinueSubmit,"controller launch",[this]{launch();});
        }
        for(auto h:results){
            auto s=runtime.state(h.id());if(s==runtime::State::Visible || s==runtime::State::Failed)continue;
            try{runtime.try_get(h);throw std::logic_error("controller result visible early");}
            catch(const content::Error& e){controller_check(e.code==content::Code::NotReady,"unexpected early state");++early;}
        }
        if(closed==4 && !transport.pending() && !reads->pending() && !Simulator->Peek_next_time() && bridge.idle()){
            if(!ordinary){device.flow->submit(120,1);return;}
            controller_check(ordinary==1 && dma==512,"local vectors reached host / ordinary route broken");finished=true;
        }
    }
    void launch(){
        controller_check(controller->chunk_size()==2,"unstable chunk config");
        for(uint64_t q=1;q<=4;++q){
            auto opened=controller->open(q,1,std::vector<float>(config.dimension,0));
            // Entire-batch rejection must have no task/credit/message side effects.
            auto before=runtime.transitions().size(),credits=controller->buffer().records().size(),messages=transport.records().size();
            bool oversized=false;
            try{controller->score(q,{{{1,content::Kind::Vector,5,1},{0,config.dimension*4ULL}}});}
            catch(const content::Error& e){oversized=e.code==content::Code::CapacityExceeded;}
            controller_check(oversized && before==runtime.transitions().size() && credits==controller->buffer().records().size() && messages==transport.records().size(),"oversize not atomic");
            auto score=controller->score(q,selections);results.push_back(score);
            runtime.release(opened); // Private query session owns its own open dependency.
            auto close=controller->close(q); // Early close must drain every chunk, including failure siblings.
            runtime.when_all({score},q,[this,score,q](auto outcome){
                if(outcome.failure){++failed;controller_check(q==1 && injected==1,"unexpected score failure");}
                else{
                    auto scores=operators::scores(runtime.try_get(score));controller_check(scores.size()==5,"semantic batch missing chunk");
                    for(size_t i=0;i<scores.size();++i)controller_check(scores[i].id==i && scores[i].distance==double(config.dimension)*(i+1)*(i+1),"controller L2 mismatch");
                    ++completed;
                }
                returned[q]=now();
            });
            runtime.when_all({close},q+10,[this,close,q](auto outcome){
                controller_check(bool(outcome.failure)==(q==1 && (mode=="open-command" || mode=="query-staged")) && (outcome.failure || returned.count(q)),"close before score settled / wrong outcome");
                runtime.release(close);++closed;
            });
        }
    }
    void verify(){
        auto fault=mode=="command" || mode=="staged" || mode=="evaluate" || mode=="result" || mode=="open-command" || mode=="query-staged";
        controller_check(finished && completed==(fault?3:4) && failed==(fault?1:0) && closed==4,"incomplete controller scenario");
        for(auto h:results)runtime.release(h);
        controller_check(!controller->buffer().used() && !controller->buffer().waiting() && !controller->port().pending() && !controller->compute().pending() &&
            !controller->sessions() && !controller->pending_protocol() && !handles->live_records() && !runtime.live_results() && !runtime.private_slots() && !reads->pending_requests(),"controller leaked resource");
        controller_check(controller->buffer().peak()<=config.capacity_bytes && early>0,"capacity or early gating");
        std::printf("CONFIG capacity=%llu dimension=%u vector_bound=%llu chunk=%llu admission=%llu block=%llu staging_rate=%llu launch=%llu flop_rate=%llu slots=%llu\n",
            config.capacity_bytes,config.dimension,config.vector_sector_bound,controller->chunk_size(),controller->admission_limit(),controller->block_bound(),config.staging_bytes_per_ns,config.launch_ns,config.flops_per_ns,config.slots);
        for(auto& r:controller->buffer().records())std::printf("CREDIT %llu %llu %llu %llu %llu %llu %u %s\n",r.time-origin,r.id,r.query,r.task,r.bytes,r.used,r.reserve,r.lifetime.c_str());
        for(auto* resource:{&controller->port(),&controller->compute()})for(auto& r:resource->records())std::printf("RESOURCE %s %llu %llu %llu %llu %llu %llu\n",r.stage.c_str(),r.query,r.task,r.submitted-origin,r.start-origin,r.end-origin,r.bytes);
        for(auto& r:controller->storage())std::printf("STORAGE %llu %llu %llu %llu %llu %llu %llu\n",r.query,r.task,r.submitted-origin,r.completed-origin,r.sector_bytes,r.requests,r.transactions);
        for(auto& r:transport.records())std::printf("MESSAGE %u %llu %llu %llu %llu %llu %llu\n",unsigned(r.kind),r.query,r.task,r.payload,r.useful,r.submitted-origin,r.arrived-origin);
        for(auto& r:runtime.transitions())std::printf("STATE %llu %llu %llu %s\n",r.time_ns-origin,r.query,r.task,runtime::name(r.state));
        std::printf("M3.3 PASS mode=%s completed=%u failed=%u peak=%llu vector_d2h=0 ordinary_d2h=%llu early=%llu live=0\n",mode.c_str(),completed,failed,controller->buffer().peak(),dma,early);
    }
};
}
