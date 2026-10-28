#pragma once
#include "model/host_cpu.h"
#undef NDEBUG
#include <cassert>
namespace ndpsim::test {
inline void host_scenario(runtime::Runtime& rt,model::HostCpu& cpu,EventBridge& bridge,EventBridge::Clock now,unsigned& done) {
    using namespace operators;
    runtime::Operation read;read.query=1;read.grant=[&rt](auto id){rt.start(id);};
    read.evaluate=[](const auto&){return fp32({4,6,3});};
    read.service=[&rt,&bridge,now](auto id){bridge.enqueue(now()+5,EventBridge::ResourceCompleteRelease,"input arrives",[&rt,id]{rt.service_done(id);});};
    auto input=rt.submit(std::move(read));
    auto distance=cpu.submit(1,{input},model::Kind::Distance,[](const auto& v){return squared_l2(fp32({1,2,3}),{v.at(0)},{7});});
    auto control=cpu.submit(1,{distance},model::Kind::Control,[](const auto& v){return candidates(v.at(0),1);});
    bridge.enqueue(6,EventBridge::ContinueSubmit,"score private",[&rt,distance]{try{rt.try_get(distance);assert(false);}catch(const content::Error& e){assert(e.code==content::Code::NotReady);}});
    rt.when_all({control},901,[&rt,input,distance,control,now,&done](runtime::Outcome o){assert(!o.failure);auto s=scores(rt.try_get(control));assert(s.size()==1 && s[0].id==7 && s[0].distance==25);assert(now()==13);for(auto h:{input,distance,control})rt.release(h);++done;});
}
}
