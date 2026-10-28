#include "pcie_scenario.h"
#include <queue>
int main() {
    try {
        uint64_t now=0,sequence=0;
        using Wake=std::tuple<uint64_t,uint64_t,uint64_t>;
        std::priority_queue<Wake,std::vector<Wake>,std::greater<Wake>> wakes;
        ndpsim::EventBridge bridge([&]{return now;},[&](auto delay,auto generation){wakes.emplace(now+delay,sequence++,generation);});
        ndpsim::test::PcieScenario scenario(bridge,[&]{return now;});
        scenario.start();bridge.start();unsigned steps=0;
        while(!wakes.empty()) {
            if(++steps>100000)throw std::logic_error("PCIe scenario stalled");
            auto [time,seq,generation]=wakes.top();wakes.pop();now=time;
            bridge.wake(generation);scenario.poll();
            bridge.require_progress(scenario.finished?0:1,"PCIe scenario");
        }
        scenario.verify();
    } catch(const std::exception& e){std::fprintf(stderr,"%s\n",e.what());return 1;}
}
