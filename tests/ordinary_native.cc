// Access only for the independent native-loop driver; MQSim source remains unmodified.
#include <iostream>
#include <sstream>
#include <fstream>
#include <vector>
#include <map>
#include <unordered_map>
#include <set>
#include <list>
#include <functional>
#include <memory>
#define private public
#include "sim/Engine.h"
#include "ssd/Address_Mapping_Unit_Page_Level.h"
#undef private
#include "ordinary_fixture.h"
int main(int argc,char** argv) {
    std::unique_ptr<ndpsim::DeviceConfig> runtime;
    unsigned qd=argc>2?32:1;
    if(argc>1 && std::string(argv[1])=="--device-xml") {
        if((argc!=7 && argc!=9) || std::string(argv[3])!="--declaring-file" || std::string(argv[5])!="--qd")throw std::invalid_argument("invalid runtime config arguments");
        if(argc==9 && std::string(argv[7])!="--requests")throw std::invalid_argument("invalid request manifest argument");
        runtime=std::make_unique<ndpsim::DeviceConfig>(argv[2],argv[4]);qd=std::stoul(argv[6]);
    }
    OrdinaryFixture fixture(!runtime && argc>1 && std::string(argv[1])=="miss",qd,false,0,runtime.get(),argc==9 && std::string(argv[7])=="--requests"?argv[8]:"");fixture.start();
    while(Simulator->_EventList->Count) {
        auto* node=Simulator->_EventList->Get_min_node();
        auto* ev=node->FirstSimEvent;
        Simulator->_sim_time=ev->Fire_time;
        while(ev) {
            if(!ev->Ignore) ev->Target_sim_object->Execute_simulator_event(ev);
            auto* consumed=ev;ev=ev->Next_event;delete consumed;
        }
        Simulator->_EventList->Remove(node); fixture.poll();
    }
    fixture.verify();
    std::puts("M1.3 PASS native loop");
}
