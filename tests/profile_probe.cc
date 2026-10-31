#include "backend/ordinary_device.h"
#include <iostream>
int main(int argc, char** argv) {
    std::string name = argc > 1 ? argv[1] : "native-small";
    const auto& p = ndpsim::device_profile(name);
    if (argc > 2) {
        bool rejected = false;
        try {
            ndpsim::OrdinaryDevice bad(p.raw_sectors, name);
        } catch (const std::invalid_argument& e) {
            rejected = std::string(e.what()).find("actual MQSim namespace") != std::string::npos;
        }
        if (!rejected)
            throw std::logic_error("actual namespace overflow accepted");
        std::cout << "PASS actual namespace overflow " << name << "\n";
        return 0;
    }
    // Reject impossible layouts before MQSim allocates metadata.
    bool rejected = false;
    try {
        ndpsim::OrdinaryDevice bad(p.raw_sectors + p.page_sectors, name);
    } catch (const std::invalid_argument&) {
        rejected = true;
    }
    if (!rejected)
        throw std::logic_error("raw capacity accepted");
    rejected = false;
    try {
        ndpsim::OrdinaryDevice bad(p.page_sectors, name, 1);
    } catch (const std::invalid_argument&) {
        rejected = true;
    }
    if (!rejected)
        throw std::logic_error("resource preflight accepted");
    // No full capacity preload: validate real namespace construction independently.
    ndpsim::OrdinaryDevice device(p.page_sectors, name);
    if (!device.namespace_sectors || device.namespace_sectors >= p.raw_sectors)
        throw std::logic_error("actual namespace capacity");
    uint64_t target = name == "ann-2g-v1" ? 2000000 : name == "ann-16g-v1" ? 20000000 : 8;
    if (target > device.namespace_sectors)
        throw std::logic_error("target layout exceeds actual namespace");
    std::cout << "PROFILE " << name << " raw_sectors=" << p.raw_sectors
              << " namespace_sectors=" << device.namespace_sectors
              << " page_sectors=" << p.page_sectors << "\n";
}
