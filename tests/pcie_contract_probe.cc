#include "transport/pcie.h"
#include "transport/conversation.h"
#include <queue>
#include <cmath>
#include <cstdio>
using namespace ndpsim;
using namespace ndpsim::transport;
static void check(bool ok,const char* why){if(!ok)throw std::logic_error(why);}
template<class F> void reject(F f) {bool rejected=false;try{f();}catch(const std::invalid_argument&){rejected=true;}check(rejected,"expected rejection");}
static Message open() {Message m;m.query=3;m.task=5;m.version=8;m.dimension=2;m.query_values={1.25f,-2};return m;}
static Message control(Kind k,uint64_t task=5) {Message m;m.kind=k;m.query=3;m.task=task;m.token=11;return m;}
static void codec() {
    auto m=open();auto bytes=encode(m);
    check(bytes.size()==72 && Bytes(bytes.begin(),bytes.begin()+8)==Bytes({'N','D','P','S',1,0,1,0}),"wrong encoded header");
    check(Bytes(bytes.end()-8,bytes.end())==Bytes({0,0,0xa0,0x3f,0,0,0,0xc0}),"wrong IEEE little endian");
    check(decode(bytes,Direction::H2D).query_values==m.query_values,"query roundtrip");
    for(size_t size=0;size<bytes.size();++size)reject([&]{decode(Bytes(bytes.begin(),bytes.begin()+size),Direction::H2D);});
    for(auto offset:{0,4,6,8,12,16,24,32,40,44,48,52,54,56}) {
        auto bad=bytes;bad[offset]^=0xff;
        // Nonzero IDs/version are legal; test zero values explicitly below.
        if(offset==16 || offset==24 || offset==56)continue;
        reject([&]{decode(bad,Direction::H2D);});
    }
    for(auto offset:{16,24,56}) {auto bad=bytes;std::fill(bad.begin()+offset,bad.begin()+offset+8,0);reject([&]{decode(bad,Direction::H2D);});}
    reject([&]{decode(bytes,Direction::D2H);});
    m.query_values[0]=INFINITY;reject([&]{encode(m);});
    auto score=control(Kind::Score,6);score.chunk=4;score.dimension=2;score.ids={2,7};
    auto result=control(Kind::ScoreResult,6);result.chunk=4;result.scores={{2,25},{7,5}};
    check(encode(score).size()==72 && encode(result).size()==80,"packed body sizes");
    auto bad=score;bad.ids={2,2};reject([&]{encode(bad);});
    auto nan=result;nan.scores[0].distance=NAN;reject([&]{encode(nan);});
    Conversation peer;peer.arrive(open());peer.arrive(control(Kind::QueryOpenAck));peer.arrive(score);
    auto wrong=result;wrong.token=12;reject([&]{peer.arrive(wrong);});
    wrong=result;wrong.chunk=5;reject([&]{peer.arrive(wrong);});
    wrong=result;std::swap(wrong.scores[0],wrong.scores[1]);reject([&]{peer.arrive(wrong);});
    reject([&]{peer.arrive(control(Kind::QueryClose,7));});
    peer.arrive(result);reject([&]{peer.arrive(result);});
    bad=score;bad.task=8;reject([&]{peer.arrive(bad);}); // reused chunk
    bad=score;bad.query=4;bad.task=8;bad.chunk=5;reject([&]{peer.arrive(bad);});
    bad=score;bad.task=8;bad.chunk=5;bad.dimension=3;reject([&]{peer.arrive(bad);});
    peer.arrive(control(Kind::QueryClose,7));peer.arrive(control(Kind::QueryCloseAck,7));
    check(!peer.pending() && !peer.sessions(),"protocol state leak");
    Conversation error;error.arrive(open());auto fail=control(Kind::Error);fail.token=0;fail.status=3;
    error.arrive(decode(encode(fail),Direction::D2H));check(!error.pending()&&!error.sessions(),"failed open leaked");
    for(auto kind:{Kind::QueryOpenAck,Kind::QueryClose,Kind::QueryCloseAck,Kind::Error}) {
        auto c=control(kind);if(kind==Kind::Error)c.status=2;
        check(encode(c).size()==48,"control size");
    }
    std::puts("CONTRACT codec=pass malformed=pass ownership=pass");
}
static void link_case(double rate) {
    using namespace Host_Components;
    Simulator->Reset();
    PCIe_Link link("isolated",nullptr,nullptr,rate);
    Simulator->AddObject(&link);Simulator->Initialize_simulation();
    uint64_t now=0,seq=0;using Wake=std::tuple<uint64_t,uint64_t,uint64_t>;
    std::priority_queue<Wake,std::vector<Wake>,std::greater<Wake>> wakes;
    EventBridge bridge([&]{return now;},[&](auto delay,auto generation){wakes.emplace(now+delay,seq++,generation);});
    PcieTransport transport(link,bridge,[&]{return now;});
    std::vector<PCIe_Link::Transfer> starts,arrivals;
    link.observe=[&](const char* stage,const auto& t){if(std::string(stage)=="start")starts.push_back(t);if(std::string(stage)=="arrive")arrivals.push_back(t);};
    PCIe_Message oversized{};oversized.Type=PCIe_Message_Type::PLATFORM;
    oversized.Destination=PCIe_Destination_Type::HOST;oversized.Payload_size=UINT32_MAX;
    oversized.Platform_arrived=[]{};
    reject([&]{link.Deliver(&oversized);});
    unsigned completed=0;
    auto receive=[&](uint64_t id,Message){check(transport.record(id).complete,"callback before record complete");++completed;};
    transport.send(open(),receive);transport.send(open(),receive);transport.send(control(Kind::QueryOpenAck),receive);
    check(completed==0 && transport.pending()==3,"inline completion");
    bridge.start();unsigned steps=0;
    while(!wakes.empty()) {
        check(++steps<100,"zero duration loop");auto [time,s,g]=wakes.top();wakes.pop();now=time;bridge.wake(g);
    }
    check(completed==3 && !transport.pending(),"missing completion");
    auto h=uint64_t(25/rate),d=uint64_t(19/rate);
    check(transport.record(1).arrived==h && transport.record(2).arrived==h*2 && transport.record(3).arrived==d,"bandwidth/FIFO/duplex formula mismatch");
    check(starts.size()==3 && arrivals.size()==3,"duplicate queue delivery");
    std::printf("ISOLATED rate=%.0f h2d=%llu,%llu d2h=%llu wire=100,100,76\n",rate,h,h*2,d);
    // Direct same-direction reentrant callback: no duplicate scheduling and exactly two arrivals.
    unsigned reentered=0;
    auto append=[&](){auto p=new PCIe_Message{};p->Destination=PCIe_Destination_Type::HOST;p->Type=PCIe_Message_Type::PLATFORM;p->Payload_size=48;
        p->Platform_arrived=[&]{++reentered;};link.Deliver(p);};
    auto p=new PCIe_Message{};p->Destination=PCIe_Destination_Type::HOST;p->Type=PCIe_Message_Type::PLATFORM;p->Payload_size=48;
    p->Platform_arrived=[&]{++reentered;append();};link.Deliver(p);
    while(!wakes.empty()) {auto [time,s,g]=wakes.top();wakes.pop();now=time;bridge.wake(g);}
    check(reentered==2,"reentrant FIFO duplicate");
    Simulator->Reset();
}
static void early_teardown() {
    using namespace Host_Components;
    Simulator->Reset();
    PCIe_Link link("early",nullptr,nullptr);Simulator->AddObject(&link);Simulator->Initialize_simulation();
    uint64_t now=0;
    EventBridge bridge([&]{return now;},[](auto,auto){});
    unsigned completed=0;
    {
        PcieTransport transport(link,bridge,[&]{return now;});
        transport.send(open(),[&](auto,Message){++completed;});
    }
    // An expired adapter is safe even if MQSim drains the envelope later.
    while(auto next=Simulator->Peek_next_time()) {now=*next;Simulator->Dispatch_current_time(now,100);}
    check(completed==0,"callback after adapter destruction");
    Simulator->Reset();
}

int main() {
    try {codec();link_case(1);link_case(2);link_case(1000000);early_teardown();
        reject([]{Host_Components::PCIe_Link bad("bad",nullptr,nullptr,0);});
        reject([]{Host_Components::PCIe_Link bad("bad",nullptr,nullptr,1,0);});
        std::puts("M3.2b CONTRACT PASS zero_ns=pass duplex=pass reentry=pass");
    }catch(const std::exception& e){std::fprintf(stderr,"%s\n",e.what());return 1;}
}
