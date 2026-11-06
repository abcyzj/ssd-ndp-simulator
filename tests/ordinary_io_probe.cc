#include "ordinary_fixture.h"
int main(int argc,char** argv) {
 try {
    std::unique_ptr<ndpsim::DeviceConfig> runtime;
    unsigned qd=argc>2?32:1;
    if(argc>1 && std::string(argv[1])=="--device-xml") {
        if(argc!=7 || std::string(argv[3])!="--declaring-file" || std::string(argv[5])!="--qd")throw std::invalid_argument("invalid runtime config arguments");
        runtime=std::make_unique<ndpsim::DeviceConfig>(argv[2],argv[4]);qd=std::stoul(argv[6]);
    }
    OrdinaryFixture fixture(!runtime && argc>1 && std::string(argv[1])=="miss",qd,false,0,runtime.get());
    bool rejected=false;
    try {fixture.window->read(0,1);} catch(const std::logic_error&) {rejected=true;}
    if(!rejected) throw std::logic_error("early measurement accepted");
    rejected=false;
    try {fixture.flow->submit(0,0);} catch(const std::invalid_argument&) {rejected=true;}
    if(!rejected) throw std::logic_error("zero extent accepted");
    fixture.start();
    unsigned steps=0;
    while(auto next=Simulator->Peek_next_time()) {
        if(++steps>100000) throw std::logic_error("stall");
        Simulator->Dispatch_current_time(*next); fixture.poll();
    }
    if(!fixture.measuring || fixture.done!=fixture.target || fixture.flow->pending()) throw std::logic_error("incomplete");
    fixture.verify();
    std::puts("M1.3 PASS");
 } catch(const std::exception& e){std::fprintf(stderr,"%s\n",e.what());return 1;}
}
