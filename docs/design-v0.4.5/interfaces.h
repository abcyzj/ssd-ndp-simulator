#pragma once
// P01 header-level design. No implementations, production include or stable ABI.
#include <cstdint>
#include <functional>
#include <optional>
#include <string>
#include <vector>

namespace ndpsim::v045 {
using Ns = std::uint64_t;
using BytesCount = std::uint64_t;
struct Quantity { std::string name, unit; std::uint64_t value; };
using Counters = std::vector<Quantity>; // Unique names; units checked by registered spec.
struct StageKey { std::uint64_t query; std::string op, stage; };
enum class EdgeKind { Data, Address, Control, Order };
enum class Location { Host, Controller };
struct Dependency { EdgeKind kind; StageKey source; std::string source_port, input; };
struct OutputDescriptor {
    std::string port, type, digest;
    BytesCount logical_bytes;
    std::uint64_t version;
};
struct Access { std::string object; std::uint64_t version, offset_bytes, length_bytes; };
struct StateVersion {
    std::string name, output_port;
    std::optional<std::string> input_port;
    std::uint64_t before, after;
};
struct Stage {
    StageKey key;
    std::string kind, context, decomposition_rule;
    std::uint64_t topological_rank, segment;
    std::optional<std::uint64_t> chunk;
    Counters work, features;
    std::vector<Dependency> dependencies;
    std::vector<Access> accesses;
    std::vector<OutputDescriptor> outputs;
    std::vector<StateVersion> state_versions;
};
enum class Cardinality { One, Many };
struct PortSpec { std::string name, type, unit; bool required; Cardinality cardinality; };
struct FeatureSpec { std::string name, unit; };
struct StageSpec {
    std::string kind, output_size_rule;
    std::vector<PortSpec> inputs, outputs;
    std::vector<std::string> contexts, decomposition_rules;
    std::vector<FeatureSpec> work_units, feature_units;
};
struct OperatorSpec {
    std::string id, version, semantic_validator;
    std::vector<Location> locations;
    std::vector<StageSpec> stages;
};
struct ObjectDescriptor {
    std::string id, type, content_sha256;
    std::uint64_t version;
    BytesCount size_bytes;
    std::vector<std::uint64_t> shape;
};
struct Operation {
    std::uint64_t query;
    std::string id, operator_id, operator_version;
    Counters semantic_work;
    std::vector<Stage> stages;
};
// Metadata only; capture callers explicitly supply all four dependency kinds.
class CaptureWriter {
public:
    virtual ~CaptureWriter() = default;
    virtual void declare_operator(const OperatorSpec&) = 0;
    virtual void declare_object(const ObjectDescriptor&) = 0;
    virtual void record(const Operation&) = 0;
    virtual void seal() = 0; // Counts/index/tail/quality checked before atomic publication.
};
class VerifiedStage; // P02 private construction after package/registry/range validation.
class EffectiveConfig; // Validated authoritative references; no payload paths.
struct Target { std::string id, hardware, os, frequency, affinity; std::uint64_t memory_bytes; };
struct CostInput {
    const VerifiedStage& stage;
    Location location;
    const Target& target;
};
struct ModelParameters { std::string canonical_json, sha256; }; // Data, never executable code.
class CostModel {
public:
    virtual ~CostModel() = default;
    virtual Ns demand(const CostInput&, const ModelParameters&) const = 0;
};
enum class Status { Ok, InvalidArgument, Unsupported, CapacityExceeded,
                    DependencyFailed, BackendFailure, VersionMismatch };
struct TransferDescriptor {
    std::string protocol, kind, size_rule;
    Location source, destination;
    Status status;
    std::uint64_t query, task, token, chunk, count;
    BytesCount header_bytes, logical_bytes, payload_bytes, wire_bytes;
};
enum class StepKind { Reserve, Read, Transfer, Compute, Release, Publish };
struct Extent { std::uint64_t lba, sectors; BytesCount logical_bytes; };
struct Step {
    std::uint64_t id;
    StepKind kind;
    std::string resource, lease, rule;
    std::vector<std::uint64_t> predecessors;
    BytesCount logical_bytes, physical_bytes;
    Ns demand_ns; // Compute/buffer/endpoint demand only; MQSim owns read/link service.
    StageKey service_key;
    std::vector<Extent> extents;
    std::optional<TransferDescriptor> transfer;
    std::vector<OutputDescriptor> outputs;
};
struct MappingPlan { StageKey stage; std::vector<Step> steps; };
class Mapping {
public:
    virtual ~Mapping() = default;
    virtual MappingPlan lower(const VerifiedStage&, const EffectiveConfig&) const = 0;
};
class Registry {
public:
    virtual ~Registry() = default;
    virtual void add_operator(OperatorSpec) = 0;
    virtual void add_mapping(std::string id, const Mapping&) = 0;
    virtual void add_model(std::string id, const CostModel&) = 0;
    // Duplicate/unknown/version/capability mismatch is an error; no Host fallback.
};
enum class Phase { Backend = 0, Release = 1, Publish = 2, Continue = 3 };
enum class State { Submitted, Dependencies, Resource, Running, ServiceDone,
                   WaitingReturn, Visible, FailedDraining, Failed };
struct CompletionToken { std::uint64_t id; }; // No business value accessor.
struct Outcome { bool success; std::string code, causal_parent; };
class Runtime {
public:
    virtual ~Runtime() = default;
    virtual CompletionToken submit(MappingPlan, std::vector<CompletionToken>) = 0;
    virtual void when_complete(CompletionToken, std::function<void(Outcome)>) = 0;
    virtual void retain(CompletionToken) = 0;
    virtual void release(CompletionToken) = 0;
    // Implementations use SST EventBridge, not a new clock or evaluate callback.
};
} // namespace ndpsim::v045
