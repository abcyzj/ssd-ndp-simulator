#pragma once
#include "operators/operators.h"
#include <functional>
#include <map>
#include <memory>
#include <string_view>

namespace ndpsim::model {
using CpuFeatures = std::map<std::string, uint64_t, std::less<>>;

struct CpuApplicability {
    std::string hardware, os;
    uint64_t memory_bytes;
    std::string frequency, affinity;
    std::string source_sha256, binary_sha256, compiler;
    std::vector<std::string> flags;
    uint64_t dimensions, records;
    std::string preparation_context;
    uint64_t workers;
};

// Only a schema-validated Profile bound to independently supplied conditions can estimate.
class CpuProfile {
public:
    static CpuProfile load(const std::string& path,
                           const std::string& expected_sha256,
                           const CpuApplicability& actual,
                           bool allow_fixture = false);

    uint64_t estimate(std::string_view op,
                      std::string_view stage,
                      const operators::Work&,
                      const CpuFeatures& extra = {}) const;

private:
    struct Data;
    explicit CpuProfile(std::shared_ptr<const Data> data) : data_(std::move(data)) {
    }
    std::shared_ptr<const Data> data_;
};

struct CpuStage {
    std::string op, stage;
    // Trusted adapter derives additional counters from visible inputs and the real evaluation.
    std::function<CpuFeatures(const std::vector<content::Bytes>&, const operators::Result&)>
        features;
};
} // namespace ndpsim::model
