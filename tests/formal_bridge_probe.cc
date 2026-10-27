#include "backend/event_bridge.h"
#undef NDEBUG
#include <cassert>
#include <vector>
struct Fixture: MQSimEngine::Sim_Object {
    Fixture():Sim_Object("formal-e1"){}
    std::vector<std::pair<uint64_t,int>> seen;
    void Validate_simulation_config() override {}
    void Start_simulation() override {
        Simulator->Register_sim_event(10,this,nullptr,1);
        Simulator->Register_sim_event(10,this,nullptr,2);
        auto* ignored=Simulator->Register_sim_event(15,this,nullptr,99); Simulator->Ignore_sim_event(ignored);
        Simulator->Register_sim_event(30,this,nullptr,8);
    }
    void Execute_simulator_event(MQSimEngine::Sim_Event* e) override {
        seen.emplace_back(Simulator->Time(),e->Type);
        if(e->Type==1 || e->Type==3) Simulator->Register_sim_event(10,this,nullptr,e->Type+2-(e->Type==3));
        if(e->Type==5) Simulator->Register_sim_event(20,this,nullptr,6);
    }
};
void formal_e1() {
    Simulator->Reset(); Fixture fixture; Simulator->AddObject(&fixture); Simulator->Initialize_simulation();
    uint64_t now=0; std::multimap<uint64_t,uint64_t> wakes;
    ndpsim::EventBridge bridge([&]{return now;},[&](auto delay,auto g){wakes.emplace(now+delay,g);});
    bridge.start();
    bridge.enqueue(20,ndpsim::EventBridge::ContinueSubmit,"external injection",[&]{
        assert(Simulator->Time()==20);
        Simulator->Register_sim_event(25,&fixture,nullptr,7);
        auto* ignored=Simulator->Register_sim_event(22,&fixture,nullptr,99); Simulator->Ignore_sim_event(ignored);
        Simulator->Register_sim_event(20,&fixture,nullptr,5);
    });
    while(!wakes.empty()) {
        auto wake=*wakes.begin(); wakes.erase(wakes.begin()); now=wake.first;
        bridge.wake(wake.second);
    }
    const std::vector<std::pair<uint64_t,int>> expected{{10,1},{10,2},{10,3},{10,4},{20,5},{20,6},{25,7},{30,8}};
    assert(fixture.seen==expected);
    std::puts("PASS formal E1 exact 8 events clocks injection Ignore earlier wake");
}
int main() {
    formal_e1();
    Simulator->Reset(); Simulator->Initialize_simulation();
    uint64_t now=0; std::vector<std::pair<uint64_t,uint64_t>> wakes;
    ndpsim::EventBridge bridge([&]{return now;},[&](auto delay,auto g){wakes.emplace_back(now+delay,g);},8);
    bridge.start();
    bridge.enqueue(30,ndpsim::EventBridge::Publish,"later",[]{});
    bridge.enqueue(20,ndpsim::EventBridge::Publish,"earlier",[]{});
    now=30;
    assert(!bridge.wake(wakes[0].second) && Simulator->Time()==0);
    now=20; assert(bridge.wake(wakes[1].second));
    now=30; assert(bridge.wake(wakes.back().second));
    try {bridge.require_progress(1,"request=7 credit=16"); return 1;}
    catch(const std::logic_error& e){assert(std::string(e.what()).find("Stalled:")!=std::string::npos); std::puts(e.what());}
    std::function<void()> loop;
    loop=[&]{bridge.enqueue(now,ndpsim::EventBridge::Publish,"loop request=8",loop);};
    loop();
    try {bridge.wake(wakes.back().second);return 2;}
    catch(const std::logic_error& e){assert(std::string(e.what()).find("Stalled:")!=std::string::npos); std::puts(e.what());}
    std::puts("PASS generation stale no clock advance and Stalled diagnostics");
}
