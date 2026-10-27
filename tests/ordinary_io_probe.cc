#include "ordinary_fixture.h"
int main(int argc,char** argv) {
 try {
    OrdinaryFixture fixture(argc>1 && std::string(argv[1])=="miss",argc>2?32:1);
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
