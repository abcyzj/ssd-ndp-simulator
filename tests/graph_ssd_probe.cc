// Test-only deterministic driver; production time is provided by SST.
#include "extensions/graph/ssd.h"
#include <map>
#include <iostream>
int main(int argc, char** argv) {
    try {
        if (argc != 2)
            throw std::runtime_error("config required");
        uint64_t now = 0;
        std::multimap<uint64_t, uint64_t> wakes;
        ndpsim::EventBridge bridge(
            [&] {
                return now;
            },
            [&](auto d, auto g) {
                wakes.emplace(ndpsim::timing::checked_add(now, d), g);
            });
        ndpsim::graph::SsdRun run(argv[1], bridge, [&] {
            return now;
        });
        run.start();
        unsigned count = 0;
        while (!wakes.empty()) {
            if (++count > 1000000)
                throw std::runtime_error("test budget");
            auto w = *wakes.begin();
            wakes.erase(wakes.begin());
            now = w.first;
            bridge.wake(w.second);
            run.poll();
        }
        run.verify();
        if (!run.success())
            throw std::runtime_error("failed run");
        std::cout << "P13 SSD PASS\n";
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
