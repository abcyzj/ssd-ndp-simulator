#include "host_scenario.h"
#include <chrono>
#include <cmath>
#include <iostream>
#include <limits>
using namespace ndpsim;
using namespace ndpsim::operators;
using namespace ndpsim::model;
template<class F> void error(content::Code c,F f){try{f();assert(false);}catch(const content::Error& e){assert(e.code==c);}}
struct Fixture {
    uint64_t now=0;std::multimap<uint64_t,uint64_t> wakes;
    EventBridge bridge{[this]{return now;},[this](auto d,auto g){wakes.emplace(now+d,g);}};
    runtime::Runtime rt{bridge,[this]{return now;}};
    Fixture(){Simulator->Reset();Simulator->Initialize_simulation();bridge.start();}
    void drain(){while(!wakes.empty()){auto w=*wakes.begin();wakes.erase(wakes.begin());now=w.first;bridge.wake(w.second);rt.require_progress();}rt.require_progress();}
};
void numerical() {
    auto r=squared_l2(fp32({1,2,3}),{fp32({4,6,3}),fp32({-1,2,4}),fp32({1,2,3})},{9,2,7});
    auto s=scores(r.bytes);assert(s[0].distance==25 && s[1].distance==5 && s[2].distance==0);
    assert(r.work.elements==9 && r.work.flops==27 && r.work.read_bytes==48 && r.work.output_bytes==36 && r.work.write_bytes==36);
    auto precise=squared_l2(fp32({16777216.f}),{fp32({-1.f})},{1});assert(scores(precise.bytes)[0].distance==281475010265089.0);
    auto order=squared_l2(fp32({0,0,0}),{fp32({1e8f,1,1})},{1});assert(scores(order.bytes)[0].distance==1e16);
    auto extreme=squared_l2(fp32({std::numeric_limits<float>::max()}),{fp32({-std::numeric_limits<float>::max()})},{1});assert(std::isfinite(scores(extreme.bytes)[0].distance));
    auto ties=squared_l2(fp32({0}),{fp32({1}),fp32({-1}),fp32({2})},{9,2,7});
    auto packed=ties.bytes;packed.insert(packed.end(),ties.bytes.begin(),ties.bytes.begin()+12);
    auto top=candidates(packed,2);auto t=scores(top.bytes);assert(t.size()==2 && t[0].id==2 && t[1].id==9);assert(top.work.candidate_checks==4 && top.work.comparisons==7 && top.work.output_bytes==24);
    assert(candidates(packed,0).bytes.empty());assert(candidates({},10).bytes.empty());
    error(content::Code::InvalidArgument,[]{fp32({std::numeric_limits<float>::infinity()});});
    error(content::Code::InvalidArgument,[]{squared_l2({}, {}, {});});
    error(content::Code::InvalidArgument,[]{squared_l2({0,0,128,127},{},{ });});
    error(content::Code::InvalidArgument,[]{squared_l2(fp32({1}),{fp32({1,2})},{1});});
    error(content::Code::InvalidArgument,[]{squared_l2(fp32({1}),{fp32({1}),fp32({2})},{1,1});});
    error(content::Code::InvalidArgument,[]{squared_l2(fp32({1}),{fp32({1})},{});});
    error(content::Code::InvalidArgument,[]{scores({1});});
    std::cout<<"NUMERIC distances=25,5,0 elements=9 flops=27 read=48 output=36 control_checks=4 comparisons=7 output=24\n";
}
void formula() {
    HostConfig c;c.distance={3,4};c.control={2,3};c.check_units=2;c.comparison_units=3;
    Work w;w.flops=27;w.candidate_checks=4;w.comparisons=8;
    assert(service_ns(w,Kind::Distance,c)==10);assert(service_ns(w,Kind::Control,c)==13);
    w.flops=0;assert(service_ns(w,Kind::Distance,c)==3);w.flops=28;assert(service_ns(w,Kind::Distance,c)==10);
    c.distance.units_per_ns=8;assert(service_ns(w,Kind::Distance,c)==7);
    c.slots=0;error(content::Code::InvalidArgument,[&]{service_ns(w,Kind::Distance,c);});c.slots=1;
    c.control.units_per_ns=0;error(content::Code::InvalidArgument,[&]{service_ns(w,Kind::Control,c);});c.control.units_per_ns=1;
    w.comparisons=UINT64_MAX;error(content::Code::Overflow,[&]{service_ns(w,Kind::Control,c);});
    c.distance={UINT64_MAX,1};error(content::Code::Overflow,[&]{service_ns(w,Kind::Distance,c);});
    std::cout<<"FORMULA distance=3+ceil(27/4)=10 control=2+ceil((4*2+8*3)/3)=13 doubled_rate=7\n";
}
std::vector<uint64_t> timing(unsigned repeats,uint64_t slots) {
    static std::vector<content::Bytes> reference(3);
    Fixture f;HostConfig c;c.slots=slots;c.distance={3,4};c.control={2,3};HostCpu cpu(f.rt,f.bridge,[&]{return f.now;},c);
    std::vector<runtime::Handle> handles;uint64_t checksum=0;auto start=std::chrono::steady_clock::now();
    for(unsigned j=0;j<3;++j)handles.push_back(cpu.submit(j+1,{},j==1?Kind::Control:Kind::Distance,[&,j](const auto&){
        Result r;for(unsigned i=0;i<repeats;++i){r=squared_l2(fp32({1,2,3}),{fp32({4,6,3}),fp32({-1,2,4}),fp32({1,2,3})},{9,2,7});checksum+=r.bytes[0];}return j==1?candidates(r.bytes,2):r;
    }));
    f.drain();auto wall=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-start).count();assert(checksum==uint64_t(repeats)*27);
    std::vector<uint64_t> times;for(auto& s:cpu.services()){times.push_back(s.end_ns);std::cout<<"SERVICE "<<slots<<" "<<s.submitted_ns<<" "<<s.start_ns<<" "<<s.end_ns<<" "<<s.service_ns<<" "<<(s.kind==Kind::Distance?"distance":"control")<<"\n";}
    for(size_t i=0;i<handles.size();++i){auto bytes=f.rt.try_get(handles[i]);assert(!bytes.empty());if(reference[i].empty())reference[i]=bytes;else assert(reference[i]==bytes);f.rt.release(handles[i]);}assert(!cpu.busy() && !f.rt.live_results());
    std::cout<<"WALL repeats="<<repeats<<" slots="<<slots<<" ns="<<wall<<"\n";return times;
}
void failures() {
    Fixture f;HostConfig c;c.distance={0,1};HostCpu cpu(f.rt,f.bridge,[&]{return f.now;},c);
    auto bad=cpu.submit(1,{},Kind::Distance,[](const auto&)->Result{throw content::Error(content::Code::InvalidArgument,"injected");});
    auto good=cpu.submit(2,{},Kind::Distance,[](const auto&){return squared_l2(fp32({0}),{fp32({1})},{1});});
    auto canceled=cpu.submit(3,{},Kind::Distance,[](const auto&)->Result{assert(false);return {};});
    f.bridge.enqueue(1,EventBridge::ContinueSubmit,"cancel queued",[&]{f.rt.fail(canceled.id(),content::Code::DependencyFailed,"cancel queued");});
    f.drain();assert(f.rt.state(bad.id())==runtime::State::Failed && f.rt.state(canceled.id())==runtime::State::Failed && f.rt.state(good.id())==runtime::State::Visible && !cpu.busy());
    for(auto h:{bad,good,canceled})f.rt.release(h);assert(!f.rt.live_results());
    // Overflow after evaluation also releases the occupied slot.
    HostConfig over;over.distance={UINT64_MAX,1};HostCpu broken(f.rt,f.bridge,[&]{return f.now;},over);
    auto h=broken.submit(4,{},Kind::Distance,[](const auto&){return squared_l2(fp32({0}),{fp32({1})},{1});});f.drain();assert(f.rt.state(h.id())==runtime::State::Failed&&!broken.busy());f.rt.release(h);
    std::cout<<"FAILURES evaluator/queued-cancel/cost-overflow cleanup=0\n";
}
int main(){numerical();formula();auto fast=timing(1,1);assert((fast==std::vector<uint64_t>{10,15,25}));assert(timing(20000,1)==fast);assert((timing(1,2)==std::vector<uint64_t>{10,5,15}));failures();
    Fixture f;HostConfig c;c.distance={3,4};c.control={1,1};HostCpu cpu(f.rt,f.bridge,[&]{return f.now;},c);unsigned done=0;test::host_scenario(f.rt,cpu,f.bridge,[&]{return f.now;},done);f.drain();assert(done==1&&!cpu.busy()&&!f.rt.live_results());std::cout<<"M2.3 PASS input=5 distance=11 control=13\n";}
