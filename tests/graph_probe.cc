#include "search/graph.h"
#undef NDEBUG
#include <cassert>
#include <iostream>
using namespace ndpsim;
struct Fixture {
    uint64_t now=0;std::multimap<uint64_t,uint64_t> wakes;
    EventBridge bridge{[this]{return now;},[this](auto d,auto g){wakes.emplace(now+d,g);}};
    runtime::Runtime rt{bridge,[this]{return now;}};
    model::HostConfig config;
    model::HostCpu cpu{rt,bridge,[this]{return now;},config};
    Fixture(){Simulator->Reset();Simulator->Initialize_simulation();bridge.start();}
    void drain(){while(!wakes.empty()){auto w=*wakes.begin();wakes.erase(wakes.begin());now=w.first;bridge.wake(w.second);rt.require_progress();}rt.require_progress();}
};
void scenario(unsigned batch,unsigned chunk,unsigned budget,bool reverse,bool disconnected=false){
    Fixture f;search::Config cfg{5,0,5,5,batch,budget,chunk};
    std::vector<float> vectors{10,-1,1,0,2};std::vector<std::vector<uint32_t>> graph{{1,2},{3},{4},{2},{1}};
    if(disconnected)graph[0].clear();
    std::vector<search::Decision> decisions;std::vector<operators::Score> result;std::map<uint32_t,unsigned> vector_reads;bool done=false;
    search::GraphSearch session(f.rt,f.cpu,[&]{return f.now;},1,cfg,operators::fp32({0}),[&](auto kind,auto id){
        if(kind==content::Kind::Vector)++vector_reads[id];auto row=graph[id];row.insert(row.begin(),row.size());
        auto b=kind==content::Kind::Vector?operators::fp32({vectors[id]}):search::ids_bytes(row);
        runtime::Operation op;op.query=1;op.grant=[&](auto task){f.rt.start(task);};op.evaluate=[b](const auto&){return b;};
        op.service=[&,id,reverse](auto task){f.bridge.enqueue(f.now+(reverse?5-id:id+1),EventBridge::ResourceCompleteRelease,"fake input arrival",[&,task]{f.rt.service_done(task);});};
        return f.rt.submit(std::move(op));},[&](auto d){decisions.push_back(d);},[&](auto scores,auto expanded){result=scores;done=true;assert(expanded<=budget);});
    session.start();f.drain();assert(done && !f.rt.live_results() && !f.cpu.busy());for(auto p:vector_reads)assert(p.second==1);
    assert(decisions.front().expand==std::vector<uint32_t>{0});
    if(disconnected){assert(result.size()==1 && result[0].id==0);}
    else if(budget==1){assert(result.size()==3);assert(decisions.back().expand.empty());}
    else {assert(result.size()==5);assert(result[0].id==3 && result[0].distance==0);assert(result[1].id==1 && result[2].id==2);assert(result[3].id==4 && result[4].id==0);
        if(batch==1)assert(decisions[1].expand==std::vector<uint32_t>{1});else assert((decisions[1].expand==std::vector<uint32_t>{1,2}));}
}
int main(){for(bool reverse:{false,true})for(unsigned chunk:{1u,16u})for(unsigned batch:{1u,2u})scenario(batch,chunk,5,reverse);scenario(2,1,1,false);scenario(1,16,5,false,true);
    auto c=search::Config{};c.chunk=0;try{search::validate(c);assert(false);}catch(const std::invalid_argument&){}
    try{search::ids({1});assert(false);}catch(const std::invalid_argument&){}
    std::cout<<"M3.1 GRAPH PASS tie/visited/cycle/batch/chunk/order/budget/disconnected/live=0\n";
}
