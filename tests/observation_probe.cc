#include "backend/observation.h"
int main() {
    ndpsim::Observation observer;
    auto emit=MQSimObservation::sink;
    emit("command_bus",0,5,0,0,0);
    emit("command_bus",3,8,0,0,1);
    emit("array",5,20,0,0,0);
    emit("array",8,22,0,0,1);
    emit("data_out_bus",25,30,0,0,0);
    emit("data_out_bus",30,35,0,0,1);
    observer.report(2,33);
}
