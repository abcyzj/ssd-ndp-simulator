// No production event engine: independently traverse every indexed stage and
// exercise the exact Plan loader/lowering under ASan without loading SST.
#include "mapping/memory.h"
#include <iostream>
int main(int argc, char** argv) {
    try {
        if (argc != 2)
            throw std::runtime_error("config required");
        ndpsim::memory::Plan plan(argv[1]);
        auto& in = plan.inputs;
        ndpsim::dag::Package package(in.package, in.receipt, in.scratch, in.limits);
        uint64_t now = 0;
        ndpsim::EventBridge bridge(
            [&] {
                return now;
            },
            [](auto, auto) {},
            1000000,
            ndpsim::EventBridge::BackendMode::PlatformOnly);
        ndpsim::timing::HostCpu cpu(
            bridge,
            [&] {
                return now;
            },
            plan.slots);
        uint64_t sum = 0, count = 0;
        for (size_t i = 0; i < package.queries().size(); ++i) {
            auto graph = package.graph(i);
            for (const auto& n : graph.nodes) {
                auto op = plan.lower(package.stage(n), cpu);
                sum = ndpsim::timing::checked_add(sum, op.duration_ns);
                ++count;
                if (op.input || op.send_return || op.resource != &cpu || op.service_key.empty())
                    throw std::runtime_error("unexpected memory adapter/resource");
            }
        }
        std::cout
            << nlohmann::json({{"count", count}, {"ns", sum}, {"pending", cpu.pending()}}).dump()
            << '\n';
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
