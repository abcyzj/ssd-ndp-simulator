#include "runtime/runtime.h"
#undef NDEBUG
#include <cassert>
#include <iostream>
using namespace ndpsim;
using namespace ndpsim::runtime;
using content::Code;
struct Fixture {
    uint64_t now=0; std::multimap<uint64_t,uint64_t> wakes;
    EventBridge bridge{[this]{return now;},[this](auto d,auto g){wakes.emplace(now+d,g);}};
    Runtime rt{bridge,[this]{return now;}};
    Fixture(){Simulator->Reset();Simulator->Initialize_simulation();bridge.start();}
    void drain(bool check=true) {
        while(!wakes.empty()) {auto w=*wakes.begin();wakes.erase(wakes.begin());now=w.first;bridge.wake(w.second);if(check)rt.require_progress();}
        if(check)rt.require_progress();
    }
    Operation op(uint64_t query,uint64_t service,uint64_t ret=0,std::vector<Handle> inputs={}) {
        Operation o;o.query=query;o.needs_return=ret!=0;o.inputs=std::move(inputs);
        o.grant=[this](auto id){rt.start(id);};
        o.evaluate=[](const auto& values){return values.empty()?Bytes{3}:Bytes{uint8_t(values[0][0]+1)};};
        o.service=[this,service](auto id){bridge.enqueue(now+service,EventBridge::ResourceCompleteRelease,"service",[this,id]{rt.service_done(id);});};
        o.send_return=[this,ret](auto id,size_t size){assert(size==1);bridge.enqueue(now+ret,EventBridge::ResourceCompleteRelease,"return",[this,id]{rt.return_done(id);});};
        return o;
    }
};
template<class F> void error(Code code,F f) {try{f();assert(false);}catch(const content::Error& e){assert(e.code==code);}}
std::vector<uint64_t> causal(uint64_t read_delay,uint64_t compute_delay,uint64_t return_delay) {
    Fixture f;View view(f.rt);std::vector<uint64_t> times;
    auto read=f.rt.submit(f.op(1,10+read_delay));
    auto compute=f.rt.submit(f.op(1,20+compute_delay,30+return_delay,{read}));
    auto independent=f.rt.submit(f.op(2,7));
    f.rt.when_all({independent},100,[&](Outcome o){assert(!o.failure);assert(view.try_get(independent)==Bytes{3});times.push_back(f.now);});
    f.rt.when_all({read,compute,compute},101,[&](Outcome o){assert(!o.failure);assert(view.try_get(compute)==Bytes{4});times.push_back(f.now);
        // A real value selects the successor; there is no pre-recorded access trace.
        auto next=f.rt.submit(f.op(view.try_get(compute)[0],5));
        f.rt.when_all({next},102,[&,next](Outcome o){assert(!o.failure);times.push_back(f.now);f.rt.release(next);});
    });
    f.bridge.enqueue(11+read_delay,EventBridge::ContinueSubmit,"private probe",[&]{assert(f.rt.private_slots()==1);error(Code::NotReady,[&]{view.try_get(compute);});});
    f.bridge.enqueue(30+read_delay+compute_delay,EventBridge::ContinueSubmit,"return probe",[&]{assert(f.rt.state(compute.id())==State::WaitingReturn);error(Code::NotReady,[&]{view.try_get(compute);});});
    f.drain();assert(times.size()==3);
    for(auto& t:f.rt.transitions())std::cout<<"STATE "<<t.time_ns<<" "<<t.query<<" "<<t.task<<" "<<name(t.state)<<"\n";
    f.rt.release(read);f.rt.release(compute);f.rt.release(independent);
    assert(f.rt.live_results()==0&&f.rt.private_slots()==0);
    std::cout<<"TIMES "<<read_delay<<" "<<compute_delay<<" "<<return_delay;for(auto t:times)std::cout<<" "<<t;std::cout<<"\n";
    return times;
}
void failures() {
    Fixture f;unsigned callbacks=0,evaluations=0;
    auto bad=f.op(10,4);bad.evaluate=[](const auto&)->Bytes{throw content::Error(Code::OutOfRange,"injected root");};
    auto root=f.rt.submit(std::move(bad));
    auto childop=f.op(10,4,0,{root});childop.evaluate=[&](const auto&){++evaluations;return Bytes{9};};
    auto child=f.rt.submit(std::move(childop));
    auto grand=f.rt.submit(f.op(10,1,0,{child}));
    auto good=f.rt.submit(f.op(20,9));
    f.rt.when_all({grand,good},201,[&](Outcome o){assert(o.failure);assert(o.failure->task==root.id()&&o.failure->causal_parent==root.id());assert(o.failure->code==Code::OutOfRange&&o.failure->query==10&&o.failure->handle==root.id()&&o.failure->time_ns==0&&o.failure->detail.find("injected root")!=std::string::npos);++callbacks;});
    f.rt.when_all({good},202,[&](Outcome o){assert(!o.failure);++callbacks;});
    bool inline_call=false;
    f.rt.when_all({},203,[&](Outcome o){assert(inline_call&&!o.failure);++callbacks;});inline_call=true;
    f.drain();assert(callbacks==3&&evaluations==0);
    error(Code::OutOfRange,[&]{f.rt.try_get(grand);});
    assert(f.rt.state(child.id())==State::Failed&&f.rt.state(grand.id())==State::Failed);
    for(auto h:{root,child,grand,good})f.rt.release(h);
    assert(f.rt.live_results()==0&&f.rt.private_slots()==0);
    std::cout<<"FAILURE PASS callbacks=3 successor_evaluations=0 live=0\n";
}
void lifetime_and_guards() {
    Fixture f;auto h=f.rt.submit(f.op(1,10));auto copy=h;auto retained=f.rt.retain(h);
    auto child=f.rt.submit(f.op(1,1,0,{h}));f.rt.release(h);
    error(Code::Released,[&]{f.rt.try_get(copy);});error(Code::Released,[&]{f.rt.release(copy);});
    error(Code::InvalidHandle,[&]{f.rt.try_get(Handle{});});
    Runtime foreign(f.bridge,[&]{return f.now;});
    error(Code::InvalidHandle,[&]{foreign.try_get(retained);});
    error(Code::InvalidHandle,[&]{f.rt.when_all({Handle{}},300,[](Outcome){});});
    error(Code::InvalidArgument,[&]{f.rt.service_done(child.id());});
    f.rt.release(retained);f.drain();assert(f.rt.try_get(child)==Bytes{4});
    error(Code::InvalidArgument,[&]{f.rt.service_done(child.id());});
    f.rt.release(child);assert(f.rt.live_results()==0);
    auto devop=f.op(2,1);devop.location=Location::DeviceBuffer;
    auto dev=f.rt.submit(std::move(devop));f.drain();
    error(Code::PermissionDenied,[&]{f.rt.try_get(dev);});
    assert(f.rt.try_get(dev,content::Consumer::ControllerExecutor)==Bytes{3});f.rt.release(dev);
    auto canceled=f.rt.submit(f.op(3,1));f.rt.fail(canceled.id(),Code::DependencyFailed,"before dispatch");f.drain();f.rt.release(canceled);
    assert(f.rt.live_results()==0);
    std::cout<<"LIFETIME PASS released/invalid/location/duplicates in-flight ownership live=0\n";
}
void stalled() {
    Fixture f;auto o=f.op(77,1);o.grant=[](auto){};auto h=f.rt.submit(std::move(o));
    try{f.drain();assert(false);}catch(const std::logic_error& e){std::string s=e.what();assert(s.find("Stalled:")!=s.npos&&s.find("query=77 task="+std::to_string(h.id())+" state=WaitingResource")!=s.npos&&s.find("awaiting grant")!=s.npos);std::cout<<s<<"\n";}
    f.rt.fail(h.id(),Code::DependencyFailed,"release stranded resource wait");f.rt.release(h);f.drain();assert(f.rt.live_results()==0);
    std::cout<<"STALL PASS contextual diagnostic and cleanup\n";
}

void more_failures() {
    Fixture f;
    // No functional execution before resource admission; fail a dependent while it waits.
    auto resource=f.op(31,1);resource.grant=[](auto){};
    auto blocked=f.rt.submit(std::move(resource));
    auto canceled=f.rt.submit(f.op(31,1,0,{blocked}));
    auto returned=f.op(32,2,9);
    returned.send_return=[&](auto id,size_t){f.rt.fail(id,Code::BackendFailure,"return adapter failure");};
    auto badreturn=f.rt.submit(std::move(returned));
    unsigned count=0;
    f.rt.when_all({badreturn},401,[&](Outcome o){assert(o.failure&&o.failure->task==badreturn.id());++count;});
    f.bridge.enqueue(1,EventBridge::ContinueSubmit,"cancel dependent",[&]{
        assert(f.rt.state(blocked.id())==State::WaitingResource&&f.rt.private_slots()==1);
        f.rt.fail(canceled.id(),Code::DependencyFailed,"cancel before dependency completes");
        f.rt.release(canceled);
        f.rt.fail(blocked.id(),Code::DependencyFailed,"resource rejected");f.rt.release(blocked);
    });
    f.drain();assert(count==1&&f.rt.private_slots()==0);
    error(Code::BackendFailure,[&]{f.rt.try_get(badreturn);});f.rt.release(badreturn);
    assert(f.rt.live_results()==0);
    // A service adapter exception also discards an already evaluated private result.
    auto badservice=f.op(33,1);badservice.service=[](auto){throw std::runtime_error("service adapter failure");};
    auto failed=f.rt.submit(std::move(badservice));f.drain();
    error(Code::BackendFailure,[&]{f.rt.try_get(failed);});f.rt.release(failed);
    assert(f.rt.live_results()==0&&f.rt.private_slots()==0);
    auto immediate=f.rt.submit(f.op(34,0));unsigned resumed=0;
    f.rt.when_all({immediate,immediate},402,[&](Outcome o){assert(!o.failure);++resumed;});
    f.rt.release(immediate);f.drain();assert(resumed==1&&f.rt.live_results()==0);
    std::cout<<"CLEANUP PASS dependency-wait cancellation return/service failure zero-time duplicate-input wait live=0\n";
}

int main(){
    auto base=causal(0,0,0);assert(base==std::vector<uint64_t>({7,60,65}));
    for(auto delays:std::vector<std::vector<uint64_t>>{{100,0,0},{0,100,0},{0,0,100}}){auto t=causal(delays[0],delays[1],delays[2]);assert(t==std::vector<uint64_t>({7,160,165}));}
    failures();lifetime_and_guards();more_failures();stalled();std::cout<<"M2.2 PASS\n";
}
