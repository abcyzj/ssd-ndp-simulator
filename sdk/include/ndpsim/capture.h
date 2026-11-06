#pragma once
// Metadata-only capture API; standard C++17, no simulator or JSON library dependency.
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace ndpsim::capture {
using Counters = std::map<std::string, std::pair<std::uint64_t, std::string>>;
struct Key { std::uint64_t query = 0; std::string op, stage; };
enum class EdgeKind { Data, Address, Control, Order };
struct Dependency { EdgeKind kind; Key source; std::string source_port, input; };
struct Output { std::string port, type; std::uint64_t logical_bytes = 0, version = 0; std::string digest; };
struct Access { std::string object; std::uint64_t version = 0, offset_bytes = 0, length_bytes = 0; };
struct StateVersion { std::string name; std::uint64_t before = 0, after = 0; std::optional<std::string> input; std::string output; };
struct Stage {
    std::string id, kind;
    std::uint64_t topological_rank = 0;
    std::vector<Dependency> dependencies;
    Counters work, features;
    std::string context;
    std::optional<std::uint64_t> chunk;
    std::uint64_t segment = 0;
    std::vector<Access> accesses;
    std::vector<Output> outputs;
    std::vector<StateVersion> state_versions;
    std::string decomposition_rule = "none/1";
};
struct Operation { std::uint64_t query = 0; std::string id, operator_id, operator_version; Counters semantic_work; std::vector<Stage> stages; };
enum class Cardinality { One, Many };
struct Port { std::string name, type, unit; bool required = true; Cardinality cardinality = Cardinality::One; };
struct StageSpec {
    std::string kind;
    std::vector<Port> inputs, outputs;
    std::map<std::string,std::string> work_units, feature_units;
    std::vector<std::string> contexts, decomposition_rules;
    std::string output_size_rule;
};
struct OperatorSpec { std::string id, version; std::vector<std::string> locations; std::vector<StageSpec> stages; std::string semantic_validator; };
struct Object { std::string id; std::uint64_t version = 0; std::string type; std::uint64_t size_bytes = 0; std::vector<std::uint64_t> shape; std::string content_sha256; };
struct Build { std::string source_sha256, binary_sha256, compiler; std::vector<std::string> flags; };
struct Provenance {
    std::string kind; // native-capture or synthetic-fixture; never inferred.
    Build capture_build;
    std::string input_content_sha256, semantic_version, extension_sha256, split;
    std::vector<std::uint64_t> query_ids;
    std::string workload, numeric_version, work_version, semantic_config_sha256;
};
// Measurement metadata only. Probe clocks/sampling orchestration belong to the workload.
struct CorrectedDuration { bool negative = false; std::uint64_t magnitude = 0; };
struct MeasurementSample {
    Key key;
    std::optional<std::uint64_t> chunk;
    std::uint64_t segment = 0;
    std::string stage_kind;
    Counters work, features;
    std::uint64_t calls = 1, start_cpu_ns = 0, end_cpu_ns = 0, raw_ns = 0;
    CorrectedDuration corrected_ns;
    bool low_signal = false;
};
struct ClockMetadata {
    std::string source;
    std::uint64_t resolution_ns = 1, empty_probe_ns = 0, empty_probe_count = 1;
};
struct SamplingPlan {
    std::uint64_t stride = 1, eligible = 0, planned = 0, observed = 0, lost = 0;
};
std::string encode_sample(const MeasurementSample&); // Canonical sample JSON + LF.
struct Limits {
    std::uint64_t line_bytes = 16*1024*1024, query_index_bytes = 16*1024*1024;
    std::uint64_t active_stages = 100000, output_bytes = 2ULL*1024*1024*1024;
};
class Sha256 {
public:
    Sha256();
    void update(std::string_view);
    std::string hexdigest() const;
private:
    void block(const unsigned char*);
    std::uint32_t h_[8];
    unsigned char buffer_[64]{};
    std::uint64_t bytes_ = 0;
    std::size_t used_ = 0;
};

// No payload API and no implicit dependencies. close() creates a complete capture
// stream; Python seal_capture performs registry/package validation and publication.
class Recorder {
public:
    Recorder(const std::filesystem::path& new_directory, Provenance, Limits = {});
    ~Recorder();
    Recorder(const Recorder&) = delete;
    Recorder& operator=(const Recorder&) = delete;
    void declare_operator(const OperatorSpec&);
    void declare_object(const Object&);
    void record(const Operation&);
    void close(const std::vector<Key>& terminals);
private:
    std::filesystem::path directory_;
    Provenance provenance_;
    Limits limits_;
    std::ofstream stream_;
    std::vector<std::string> operators_, objects_;
    std::vector<std::string> operator_keys_, object_keys_;
    std::uint64_t bytes_ = 0;
    Sha256 operations_hash_;
    bool started_ = false, closed_ = false, failed_ = false;
    std::mutex mutex_;
    void require_open() const;
};

std::string sha256(std::string_view);
} // namespace ndpsim::capture
