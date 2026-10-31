#pragma once
#include "observation/history.h"
#include "operators/operators.h"
#include "runtime/runtime.h"
namespace ndpsim::model {
enum class Kind { Distance, Control };
struct Cost {
    uint64_t setup_ns = 0, units_per_ns = 1;
};
struct HostConfig {
    uint64_t slots = 1;
    Cost distance, control;
    uint64_t check_units = 1, comparison_units = 1;
    std::string source = "configured_unvalidated";
};
uint64_t service_ns(const operators::Work&, Kind, const HostConfig&);
struct Service {
    uint64_t task, submitted_ns, start_ns, end_ns, service_ns;
    Kind kind;
    operators::Work work;
    uint64_t query = 0, ready_ns = 0;
};
// Trusted invoke adapter. Inputs must already be visible HostMemory runtime handles.
// Owner and Runtime/bridge outlive all queued callbacks. No standalone clock or PCIe fee.
class HostCpu {
public:
    using Evaluate = std::function<operators::Result(const std::vector<content::Bytes>&)>;
    HostCpu(runtime::Runtime&, EventBridge&, EventBridge::Clock, HostConfig);
    runtime::Handle submit(uint64_t query, std::vector<runtime::Handle>, Kind, Evaluate);
    const std::vector<Service>& services() const {
        return services_;
    }
    uint64_t busy() const {
        return busy_;
    }

public:
    History<Service>& observations() {
        return services_;
    }

private:
    struct Job {
        uint64_t task, submitted;
        Kind kind;
        std::shared_ptr<operators::Work> work;
    };
    runtime::Runtime& runtime_;
    EventBridge& bridge_;
    EventBridge::Clock now_;
    HostConfig config_;
    std::deque<Job> queue_;
    uint64_t busy_ = 0;
    History<Service> services_;
    void pump();
};
} // namespace ndpsim::model
