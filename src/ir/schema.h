#pragma once
#include <nlohmann/json.hpp>
#include <cstdint>
#include <stdexcept>
#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace ndpsim::ir {
using Json = nlohmann::json;
inline constexpr const char* version = "ndpsim-ir/1";
enum class Kind { Memory, Compute, Control, Sync, External };
enum class Operator {
    QueryReady,
    ReadAdjacency,
    ScoreL2,
    MergeCandidates,
    SelectFrontier,
    DedupNeighbors,
    Join
};
enum class Location { HostMemory, DeviceBuffer, SSDStorage };
enum class Placement { Host, Controller };
enum class DependencyKind { Address, Data, Control, Order };
enum class TypeName { U32, U64, Bool, IdsU32, Fp32, ScoresIdF64, Adjacency, QueryState, Token };
struct Dimension {
    uint64_t extent;
    bool dynamic;
};
struct Type {
    TypeName name;
    std::vector<Dimension> shape;
};
struct Identity {
    std::string name, version, sha256;
};
struct Scope {
    std::string run;
    uint64_t query, query_ordinal, iteration, semantic_batch;
    std::optional<uint64_t> chunk;
    std::optional<std::string> stage;
};
struct OutputRef {
    uint64_t producer_op;
    std::string port;
    Type type;
};
struct Slice {
    std::string object_id, field;
    uint64_t content_version;
    std::variant<std::vector<uint32_t>, OutputRef> indices;
    uint64_t offset = 0, length = 0; // length=0 means the entire logical record
};
struct Literal {
    std::variant<uint64_t, bool, std::vector<uint32_t>> value;
};
struct StateRef {
    uint64_t query;
    std::string state_id;
    uint64_t version;
};
struct ValueRef {
    Type type;
    std::variant<Slice, OutputRef, Literal, StateRef> reference;
};
struct Input {
    std::string port;
    ValueRef value;
};
struct Port {
    std::string name;
    Type type;
    Location location;
};
struct Dependency {
    uint64_t producer_op;
    DependencyKind kind;
    std::string port;
    uint64_t state_version = 0;
};
struct DataObject {
    std::string object_id, object_type, content_ref, record_schema;
    Identity content_identity;
    uint64_t content_version, element_count, width;
    TypeName element_type;
    Json extensions;
};
struct Operation {
    uint64_t op_id;
    Scope scope;
    Kind kind;
    Operator op;
    uint64_t operator_version;
    Placement placement;
    std::vector<Input> inputs;
    std::vector<Port> outputs;
    std::vector<Dependency> deps;
    std::vector<Slice> access;
    Json params, workload; // strict operator-specific records, validated before construction
    std::vector<std::string> effects;
    Json schedule, provenance, extensions;
};
struct Manifest {
    std::string ir_version, execution_engine, execution_mode, workload_type, run, catalog_sha256;
    Identity frontend, source;
    Json artifacts, search, mapping, dependencies, diagnostics, operator_set, extensions;
};
struct Document {
    Manifest manifest;
    std::vector<DataObject> objects;
    std::vector<Operation> operations;
};
struct Error : std::runtime_error {
    std::string code, path;
    uint64_t query, op, producer;
    std::optional<uint64_t> time_ns;
    Error(std::string code,
          std::string path,
          std::string detail,
          uint64_t query = 0,
          uint64_t op = 0,
          uint64_t producer = 0);
    Json diagnostic() const;
};
// Structural/schema validation plus locally knowable semantic checks. No runtime,
// visibility, ownership, or dependency admission state is created here (V03-2).
Document parse(const Json&);
Json encode(const Document&);
void validate(const Document&); // same checks for programmatically constructed values
// context contains only trusted, already admitted active producers.
void validate_submission(const Document& context, const Operation&);
Json read_json(const std::string& path); // also rejects duplicate JSON object keys
const Json& schema();
const Json& capabilities();
std::string catalog_sha256();
} // namespace ndpsim::ir
