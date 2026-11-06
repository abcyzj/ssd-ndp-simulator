#pragma once
#include "model/cost/cost_model.h"
#include "dag/executor.h"
#include <nlohmann/json.hpp>
namespace ndpsim::memory {
// Only metadata and immutable cost descriptions. No storage backend or payload.
class Plan {
public:
    explicit Plan(const std::filesystem::path& config);
    dag::HostInputs inputs;
    uint64_t slots;
    std::filesystem::path output;
    nlohmann::json snapshot;
    timing::Operation lower(const dag::Stage&, timing::HostCpu&);
    nlohmann::json last_demand;

private:
    cost::Registry registry_;
    std::unique_ptr<cost::Profile> profile_;
    cost::Target target_;
    nlohmann::json binding_, rules_;
    std::string context_;
};
} // namespace ndpsim::memory
