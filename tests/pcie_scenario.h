#pragma once
#include "backend/ordinary_device.h"
#include "transport/pcie.h"
#include "transport/conversation.h"
#include "runtime/runtime.h"
#include <cstdio>

namespace ndpsim::test {
using namespace transport;
class PcieScenario {
public:
    EventBridge& bridge;
    std::function<uint64_t()> now;
    OrdinaryDevice device{64};
    runtime::Runtime runtime;
    PcieTransport transport;
    Conversation conversation;
    bool status_done=false, closing=false;
    bool begun=false, finished=false, score_ready=false, result_sent=false;
    unsigned ordinary_done=0, result_visible=0;
    uint64_t origin=0, result_id=0, early=0;
    runtime::Handle handle;
    struct Event {std::string stage;Host_Components::PCIe_Link::Transfer t;};
    std::vector<Event> events;
    explicit PcieScenario(EventBridge& b,std::function<uint64_t()> clock):bridge(b),now(std::move(clock)),
        runtime(b,now),transport(*device.link,b,now) {
        device.flow->completed=[this](ExternalFlow::Completion c){
            if(begun) {++ordinary_done;std::printf("MIX_IO %llu %llu %u %llu\n",c.token,c.lba,c.sectors,c.time-origin);}
        };
        device.link->observe=[this](const char* stage,const auto& t){
            if(!begun)return;
            events.push_back({stage,t});
            // Inject the result while the native data DMA is occupying D2H.
            if(std::string(stage)=="start" && t.destination==Host_Components::PCIe_Destination_Type::HOST &&
               t.type==Host_Components::PCIe_Message_Type::WRITE_REQ && t.payload==512 && !result_sent) {
                bridge.enqueue(now(),EventBridge::ContinueSubmit,"mix result into active native DMA",[this]{send_result();});
            }
        };
    }
    void start(){device.start();}
    Message make(Kind k,uint64_t task=1) {
        Message m;m.kind=k;m.query=1;m.task=task;m.token=k==Kind::QueryOpen?0:7;return m;
    }
    uint64_t send(Message m,PcieTransport::Receive receive) {
        auto id=transport.send(m,[this,receive=std::move(receive)](auto id,Message arrived) mutable {
            conversation.arrive(arrived);receive(id,std::move(arrived));
        });
        if(transport.record(id).complete)throw std::logic_error("send completed inline");
        ++early;return id;
    }
    void poll() {
        if(!begun && device.window->advance(bridge.idle(),now())) {
            begun=true;origin=now();
            bridge.enqueue(now(),EventBridge::ContinueSubmit,"mixed PCIe launch",[this]{
                runtime::Operation op;op.query=1;op.needs_return=true;
                op.evaluate=[](const auto&){return content::Bytes{25,5};};
                op.grant=[this](auto t){runtime.start(t);};
                op.service=[this](auto t){runtime.service_done(t);};
                op.send_return=[](auto,size_t){};
                handle=runtime.submit(std::move(op));
                runtime.when_all({handle},1,[this](auto outcome){
                    if(outcome.failure) throw std::logic_error("unexpected runtime failure");
                    if(runtime.try_get(handle)!=content::Bytes({25,5}))throw std::logic_error("wrong visible result");
                    if(!transport.record(result_id).complete)throw std::logic_error("result visible before PCIe arrival");
                    std::printf("RESULT_VISIBLE time=%llu arrived=%llu\n",now()-origin,transport.record(result_id).arrived-origin);
                    ++result_visible;runtime.release(handle);
                    close_if_ready();
                });
                // One native command in front of Open, another behind it: actual shared H2D FIFO.
                device.flow->submit(7,1);
                auto open=make(Kind::QueryOpen);open.dimension=32;open.version=1;open.query_values.assign(32,1.25f);
                send(open,[this](auto,Message m){
                    if(m.query_values!=std::vector<float>(32,1.25f))throw std::logic_error("query corruption");
                    send(make(Kind::QueryOpenAck),[this](auto,Message ack){
                        if(ack.token!=7 || ack.task!=1)throw std::logic_error("wrong OpenAck");
                        auto score=make(Kind::Score,2);score.dimension=32;score.chunk=9;score.ids={4,8};
                        send(score,[this](auto,Message m){
                            if(m.ids!=std::vector<uint32_t>({4,8}))throw std::logic_error("command corruption");
                            score_ready=true;
                        });
                    });
                });
                device.flow->submit(16,1);
            });
        }
        if(result_id && !transport.record(result_id).complete) {
            try {runtime.try_get(handle);throw std::logic_error("result visible early");}
            catch(const content::Error& e){if(e.code!=content::Code::NotReady)throw;++early;}
        }
    }
    void send_result() {
        if(!score_ready || result_sent)throw std::logic_error("result before command arrival");
        result_sent=true;
        if(runtime.state(handle.id())!=runtime::State::WaitingReturn || !runtime.private_slots())
            throw std::logic_error("missing private precomputed result");
        auto result=make(Kind::ScoreResult,2);result.chunk=9;result.scores={{4,25},{8,5}};
        result_id=send(result,[this](auto,Message m){
            if(m.scores.size()!=2 || m.scores[0].id!=4 || m.scores[0].distance!=25 || m.scores[1].distance!=5)
                throw std::logic_error("result corruption");
            runtime.return_done(handle.id());
        });
        // A rejected command and its Error both use the real queues.
        auto rejected=make(Kind::Score,4);rejected.dimension=32;rejected.chunk=10;rejected.ids={12};
        send(rejected,[this](auto,Message){
            auto error=make(Kind::Error,4);error.status=2;
            send(error,[this](auto,Message m){
                if(m.status!=2)throw std::logic_error("status corruption");
                status_done=true;close_if_ready();
            });
        });
    }
    void close_if_ready() {
        if(!status_done || !result_visible || closing)return;
        closing=true;
        send(make(Kind::QueryClose,3),[this](auto,Message m){
            if(m.token!=7)throw std::logic_error("wrong close token");
            send(make(Kind::QueryCloseAck,3),[this](auto,Message){finished=true;});
        });
    }

    void verify() {
        if(!finished || ordinary_done!=2 || result_visible!=1 || transport.pending() || runtime.live_results() || conversation.pending() || conversation.sessions() || early<8)
            throw std::logic_error("incomplete mixed PCIe scenario");
        for(auto& e:events) {
            auto& t=e.t;
            std::printf("PCIE %s %llu %llu %u %u %llu %llu %llu %llu\n",e.stage.c_str(),t.id,t.platform_id,
                unsigned(t.destination),unsigned(t.type),t.time-origin,t.payload,t.wire,t.service);
        }
        for(auto& r:transport.records()) {
            std::printf("ENVELOPE %llu %u %u %llu %llu %llu %llu %llu %llu\n",r.id,unsigned(r.kind),unsigned(r.direction),
                r.query,r.task,r.payload,r.useful,r.submitted-origin,r.arrived-origin);
        }
        std::printf("M3.2b PASS ordinary=2 envelopes=%zu early=%llu live=0\n",transport.records().size(),early);
    }
};
}
