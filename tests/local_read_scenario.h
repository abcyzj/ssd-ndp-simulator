#pragma once
#include "backend/local_read.h"
#include "backend/ordinary_device.h"

namespace ndpsim::test {
inline void local_check(bool ok,const char* detail) {if(!ok)throw std::logic_error(detail);}
class LocalScenario {
public:
    OrdinaryDevice device{512};
    content::ObjectRef v{1,content::Kind::Vector,0,1},w{1,content::Kind::Vector,1,1};
    content::Bytes bytes=content::Bytes(1040,17),other=content::Bytes(40,23);
    content::ContentStore store{{{v,bytes},{w,other}}};
    content::Layout layout{store,512,{{v,7*512},{w,32*512}}};
    content::Handles handles{store};
    std::unique_ptr<LocalReads> reads;
    unsigned done=0,ordinary=0;uint64_t dma=0,origin=0;
    bool launched=false,finished=false;std::string mode;
    std::vector<LocalReads::Request> held;
    LocalScenario(EventBridge& bridge,EventBridge::Clock now,std::string inject=""):mode(std::move(inject)),now_(now) {
        host_=static_cast<SSD_Components::Host_Interface_NVMe*>(device.device->Host_interface);
        reads=std::make_unique<LocalReads>(*host_,bridge,now);
        device.root->data_arrived=[this](uint64_t a,unsigned n){dma+=n;device.flow->data(a,n);};
        device.flow->completed=[this](ExternalFlow::Completion c){
            if(launched) {++ordinary;local_check(!c.write && c.lba==48 && c.sectors==1,"ordinary regression extent");}
        };
    }
    void start() {device.start();}
    void poll() {
        if(!launched && device.window->advance(!Simulator->Peek_next_time(),now_())) {
            origin=now_();launched=true;dma=0;
            const auto initial_pending=host_->Pending_local_reads();
            // Whole-plan validation must reject the later invalid extent before any I/O.
            content::Layout bad(store,UINT64_MAX/512,{{v,7*512},{w,1000000000ULL*512}});
            bool rejected=false;
            try {reads->submit(bad,{{v,{0,1040}},{w,{0,40}}},{1,1,0},[](auto){});}
            catch(const content::Error& e) {rejected=e.code==content::Code::InvalidArgument;}
            local_check(rejected && !reads->pending() && host_->Pending_local_reads()==initial_pending && !Simulator->Peek_next_time(),"partial invalid submission");
            for(unsigned i=0;i<2;++i) {
                std::vector<content::Selection> selections{{v,{0,1040}},{w,{0,40}}};
                auto h=handles.create(selections,content::Location::DeviceBuffer,{i+1,i+11,now_()});
                auto pin=handles.pin(h);handles.release(h); // in-flight owner survives caller release
                reads->submit(layout,selections,{i+1,i+11,0},[this,pin,i](LocalReads::Completion c){
                    local_check(c.context.query==i+1 && c.context.task==i+11 && c.context.time_ns==origin,"context lost");
                    local_check(c.requests.size()==2 && c.plan.useful_bytes==1080 && c.plan.sector_bytes==2048,"parent aggregation");
                    local_check(!dma && !device.flow->pending(),"local vector DMA reached host");
                    bool pending=false;
                    try {handles.try_get(pin,content::Consumer::ControllerExecutor,1);}
                    catch(const content::Error& e){pending=e.code==content::Code::NotReady;}
                    local_check(pending,"storage completion published before staging");
                    for(const auto& r:c.requests) {
                        std::printf("LOCAL %llu %llu %llu %llu %llu %u %u %llu %llu\n",c.operation,c.context.query,c.context.task,r.token,r.lba,r.sectors,r.bytes,r.submitted,r.time);
                        for(const auto& t:r.transactions)
                            std::printf("TRANSACTION %llu %llu %llu %u %llu %llu\n",r.token,t.id,t.lpa,t.bytes,t.bitmap,t.time);
                    }
                    handles.release(pin);++done;
                    if(done==2) {
                        local_check(!reads->pending() && !reads->pending_requests() && !host_->Pending_local_reads() && !handles.live_records(),"retained local objects");
                        std::printf("LOCAL_DRAIN operations=2 requests=4 transactions=6 vector_d2h=%llu live=0\n",dma);
                        // Ordinary path remains usable after local requests, including real data and CQ.
                        device.window->read(48,1);
                    }
                });
            }
            auto diagnostic=reads->diagnostic();
            local_check(diagnostic.find("query=1 task=11")!=std::string::npos && diagnostic.find("native_request=")!=std::string::npos,
                        "missing in-flight failure diagnostics");
        }
        if(mode.empty()) reads->poll();
        auto completions=mode.empty()?std::vector<LocalReads::Request>{}:host_->Take_local_completions();
        if(mode=="reverse") {
            for(auto& c:completions) held.push_back(std::move(c));
            completions.clear();
            if(held.size()==4) {completions.swap(held);std::reverse(completions.begin(),completions.end());}
        }
        for(auto& c:completions) {
            if(mode=="fault") ++c.bytes;
            if(mode=="transaction") c.transactions.front().completed=false;
            if(mode=="unknown") c.token=UINT64_MAX;
            if(mode=="duplicate") {reads->accept(c);reads->accept(c);}
            reads->accept(std::move(c));
        }
        if(done==2 && ordinary==1 && !Simulator->Peek_next_time() && !device.flow->pending()) {
            local_check(dma==512,"ordinary DMA missing/double charged");finished=true;
        }
    }
    void verify() {
        local_check(finished && done==2 && ordinary==1 && !reads->pending() && !handles.live_records(),"local scenario incomplete");
        std::puts("M3.2a PASS operations=2 requests=4 transactions=6 vector_d2h=0 ordinary_d2h=512 live=0");
    }
private:
    EventBridge::Clock now_;SSD_Components::Host_Interface_NVMe* host_;
};
}
