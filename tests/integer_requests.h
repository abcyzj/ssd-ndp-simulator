#pragma once
#include "sim/Engine.h"
#include <cstdio>
struct IntegerRequests : MQSimEngine::Sim_Object {
    IntegerRequests():Sim_Object("integer-requests"){}
    unsigned completed=0;
    void Validate_simulation_config() override {}
    void Start_simulation() override {
        for(int id=1;id<=4;++id) Simulator->Register_sim_event(id<=2?10:20,this,nullptr,id);
    }
    void Execute_simulator_event(MQSimEngine::Sim_Event* e) override {
        if(e->Type<=4) Simulator->Register_sim_event(Simulator->Time()+7,this,nullptr,e->Type+10);
        else { ++completed; std::printf("REQUEST %d %llu\n",e->Type-10,(unsigned long long)Simulator->Time()); }
    }
};
