#include "model/controller.h"
#include <iostream>
using namespace ndpsim;
void check(bool ok){if(!ok)throw std::logic_error("resource assertion");}
template<class F> void reject(content::Code code,F fn){bool rejected=false;try{fn();}catch(const content::Error& e){rejected=e.code==code;}check(rejected);}
int main(){try{
    Simulator->Reset();Simulator->Initialize_simulation();
    uint64_t now=0;std::multimap<uint64_t,uint64_t> wakes;
    EventBridge bridge([&]{return now;},[&](auto d,auto g){wakes.emplace(now+d,g);});
    auto drain=[&]{while(!wakes.empty()){auto w=*wakes.begin();wakes.erase(wakes.begin());now=w.first;bridge.wake(w.second);}};
    model::BufferModel buffer(bridge,[&]{return now;},10);
    unsigned grants=0;uint64_t first=0;
    reject(content::Code::CapacityExceeded,[&]{buffer.reserve(11,{1,1,0},"oversize",[](auto){});});
    check(!buffer.used() && !buffer.waiting());
    buffer.reserve(8,{1,1,0},"input",[&](auto id){++grants;first=id;});
    buffer.reserve(7,{1,2,0},"block",[&](auto id){++grants;buffer.release(id,7);});
    buffer.reserve(2,{1,3,0},"block",[&](auto id){++grants;buffer.release(id,2);});
    check(buffer.used()==8 && buffer.waiting()==2 && grants==0); // FIFO: tiny request cannot bypass.
    bridge.start();drain();check(grants==1);
    buffer.release(first,2);check(buffer.used()==6 && buffer.waiting()==2);
    buffer.release(first,3);check(buffer.used()==10 && buffer.waiting()==1);
    drain();check(grants==3 && buffer.used()==3 && !buffer.waiting());
    buffer.release(first,3);reject(content::Code::InvalidArgument,[&]{buffer.release(first,1);});
    check(!buffer.used() && buffer.peak()==10);
    for(uint64_t slots:{1,2}){
        model::FifoResource resource(bridge,[&]{return now;},slots);
        auto origin=now;unsigned begun=0,done=0;
        for(uint64_t d:{10,5,10})resource.submit({1,d,0},"test",d,d,[&]{++begun;},[&]{++done;});
        check(!begun && !done);drain();check(done==3 && begun==3 && !resource.pending());
        auto r=resource.records();check(r.size()==3);
        check(r[0].start==origin && r[0].end==origin+10);
        check(r[1].start==origin+(slots==1?10:0) && r[1].end==origin+(slots==1?15:5));
        check(r[2].start==origin+(slots==1?15:5) && r[2].end==origin+(slots==1?25:15));
        resource.submit({1,4,0},"zero",0,0,{},[&]{++done;});check(done==3);drain();check(done==4);
    }
    reject(content::Code::Overflow,[]{model::checked_add(UINT64_MAX,1);});
    reject(content::Code::Overflow,[]{model::checked_mul(UINT64_MAX,2);});
    std::cout<<"M3.3 RESOURCE PASS FIFO=10,15,25 slots2=10,5,15 zero=async peak=10 final=0 oversize=CapacityExceeded\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
