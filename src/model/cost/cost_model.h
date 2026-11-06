#pragma once
// Timing-only model interface. Parameters are JSON data, never loaded code.
// No operator implementation, payload, event queue or execution-host identity.
#include "ndpsim/capture.h"
#include <functional>
#include <memory>
#include <stdexcept>

namespace ndpsim::cost {
using Ns = std::uint64_t;
using Counters = capture::Counters;
struct Error : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct Range {
    std::string name, unit;
    std::uint64_t minimum, maximum;
};
struct Target {
    std::string id, hardware, os, frequency, affinity;
    std::uint64_t memory_bytes = 0;
};
struct Input {
    capture::Key key;
    std::string operator_id, operator_version, stage_kind, context, location;
    Counters work, features;
    Target target;
    // Canonical ndpsim-contract/2 binding JSON, required only by measured tables.
    std::string binding_json;
};
using Estimate = std::function<Ns(const std::vector<std::uint64_t>&)>;
// compile validates parameters and the entire declared domain, not just this call.
using Compile = std::function<Estimate(const std::string&, const std::vector<Range>&)>;
class Registry {
public:
    Registry();
    ~Registry();
    Registry(const Registry&) = delete;
    Registry& operator=(const Registry&) = delete;
    void add_operator(capture::OperatorSpec);
    // ID includes version. Schema is validated by the supported schema subset;
    // semantic cross-field/domain checks remain the compiler's responsibility.
    void add_formula(std::string id, std::string parameter_schema_json, Compile);
    std::string parameter_schema_sha256(const std::string& id) const;

private:
    struct Data;
    std::shared_ptr<Data> data_;
    friend class Profile;
};
void register_builtin_formulas(Registry&);
class Profile {
public:
    // raw bytes are bound to expected_sha256. All rules eagerly validated.
    static Profile parse(const std::string& raw,
                         const std::string& expected_sha256,
                         const Registry&,
                         const Target& target,
                         bool allow_fixture = false);
    Ns demand(const Input&) const;

private:
    struct Data;
    explicit Profile(std::shared_ptr<const Data> data) : data_(std::move(data)) {
    }
    std::shared_ptr<const Data> data_;
};
} // namespace ndpsim::cost
