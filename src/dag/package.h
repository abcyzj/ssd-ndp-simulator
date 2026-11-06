#pragma once
#include "ndpsim/capture.h"
#include <filesystem>
#include <memory>
#include <vector>

namespace ndpsim::dag {
struct Limits {
    uint64_t line_bytes = 16 * 1024 * 1024, query_index_bytes = 16 * 1024 * 1024;
    uint64_t query_stages = 100000, total_bytes = 2ULL * 1024 * 1024 * 1024;
    uint64_t scratch_bytes = 4ULL * 1024 * 1024 * 1024;
};
struct Receipt {
    std::filesystem::path report;
    std::string report_sha256, package_sha256, registry_sha256;
    bool allow_fixture = false;
    std::filesystem::path semantic_report;
    std::string semantic_sha256;
};
struct Query {
    uint64_t id, stages, index_bytes;
};
struct Node {
    capture::Key key;
    uint64_t rank, offset, length;
    std::string stage_sha256;
    std::vector<capture::Dependency> dependencies;
    bool terminal = false;
};
struct Graph {
    Query query;
    std::vector<Node> nodes;
    std::vector<std::vector<size_t>> successors;
};
struct Stage {
    capture::Key key;
    std::string operator_id, operator_version;
    capture::Stage metadata;
};
// P02 receipt is externally hash-bound; this reader additionally verifies source
// bytes, indexed stages and complete query topology before target service.
class Package {
  public:
    Package(const std::filesystem::path &, Receipt, const std::filesystem::path &new_scratch,
            Limits = {});
    ~Package();
    Package(const Package &) = delete;
    Package &operator=(const Package &) = delete;
    const std::vector<Query> &queries() const;
    Graph graph(size_t query_index); // Bounded complete graph; no details/payload kept.
    Stage stage(const Node &);       // One operation at a time, random access, no file window.
    uint64_t detail_reads() const;
    uint64_t peak_detail_bytes() const;
    uint64_t scratch_bytes() const;

  private:
    struct Data;
    std::unique_ptr<Data> data_;
};
} // namespace ndpsim::dag
