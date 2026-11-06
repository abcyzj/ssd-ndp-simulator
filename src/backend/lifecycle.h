#pragma once
#include <memory>
#include <string>
#include <cstdint>

namespace ndpsim {
class DeviceConfig;
// M1.1 owns construction/initialization/teardown only; no I/O or event dispatch.
class LifecycleBackend {
public:
    explicit LifecycleBackend(unsigned device_count = 1,
                              const std::string& profile = "native-small");
    explicit LifecycleBackend(const DeviceConfig&, unsigned device_count = 1);
    ~LifecycleBackend();
    LifecycleBackend(const LifecycleBackend&) = delete;
    LifecycleBackend& operator=(const LifecycleBackend&) = delete;
    void initialize();
    void finish();
    uint64_t time_ns() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace ndpsim
