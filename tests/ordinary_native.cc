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
    OrdinaryFixture fixture(argc>1 && std::string(argv[1])=="miss",argc>2?32:1);fixture.start();
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
