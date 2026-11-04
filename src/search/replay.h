#pragma once

#include "ir/schema.h"
#include "model/host_cpu.h"
#include "search/graph.h"
#include <fstream>
#include <set>

namespace ndpsim::search {
// The runner preflights a private package snapshot. A session consumes one shard,
// retaining only its current record and the actual live search state.
class Replay {
public:
    using Read = std::function<content::Bytes(content::Kind, uint32_t, uint64_t)>;
    using Observe = std::function<void(const ir::Json&)>;
    using Fail = std::function<void(std::string)>;
    Replay(runtime::Runtime&,
           model::HostCpu&,
           Config,
           uint64_t query,
           uint32_t original,
           uint32_t dimension,
           std::ifstream&,
           ir::Json index,
           std::function<content::Bytes()> load_query,
           Read,
           Observe,
           GraphSearch::Trace,
           GraphSearch::Done,
           Fail);
    void start();

private:
    using Bytes = content::Bytes;
    using Work = operators::Work;
    using Next = std::function<void(Bytes, Work)>;
    void guard(const std::function<void()>&);
    void advance();
    void score_chunk();
    void verify(const Bytes&, const std::vector<uint32_t>&, size_t, const Work& = {});
    void state(const std::vector<uint32_t>&);
    void access(const char*, const std::vector<uint32_t>&, uint64_t);
    void service(const char* stage, model::CpuFeatures, std::function<operators::Result()>, Next);
    void emit(ir::Json);
    static std::vector<uint32_t> score_ids(const Bytes&);

    runtime::Runtime& rt_;
    model::HostCpu& cpu_;
    Config config_;
    uint64_t query_;
    uint32_t original_, dimension_;
    std::ifstream& input_;
    ir::Json index_, row_;
    std::function<Bytes()> load_query_;
    Bytes query_bytes_, rank_, batch_, joined_;
    Read read_;
    Observe observe_;
    GraphSearch::Trace trace_;
    GraphSearch::Done done_;
    Fail fail_;
    std::string op_;
    uint64_t position_, end_, local_ = 0, version_ = 0;
    uint64_t task_ = 0;
    size_t offset_ = 0, adjacency_ = 0;
    std::vector<uint32_t> fresh_, chosen_, group_;
    std::set<uint32_t> scored_, expanded_;
    std::vector<Bytes> vectors_;
    std::vector<std::vector<uint32_t>> neighbors_;
    Work score_work_;
};
} // namespace ndpsim::search
