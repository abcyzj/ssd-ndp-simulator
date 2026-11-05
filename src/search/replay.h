#pragma once

#include "ir/schema.h"
#include "model/controller.h"
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
           GraphSearch::Read ssd_read,
           model::Controller*,
           bool legacy_cost,
           Observe,
           GraphSearch::Trace,
           GraphSearch::Done,
           Fail,
           std::function<bool()> cancelled);
    void start();

private:
    using Bytes = content::Bytes;
    using Work = operators::Work;
    using Next = std::function<void(Bytes, Work)>;
    void guard(const std::function<void()>&);
    void advance();
    void score_chunk();
    void controller_score();
    void legacy_host_score();
    void reserve_legacy_control();
    void score_ready();
    void init_query();
    void finish();
    void append_scores(Bytes, size_t, std::function<void()>);
    void mark(const std::vector<uint32_t>&, std::set<uint32_t>&);
    void await(runtime::Handle, std::function<void(Bytes)>);
    void await_all(std::vector<runtime::Handle>, std::function<void(std::vector<Bytes>)>);
    void verify(const Bytes&, const std::vector<uint32_t>&, size_t, const Work& = {});
    void state(const std::vector<uint32_t>&);
    void access(const char*, const std::vector<uint32_t>&, uint64_t);
    void service(const char* stage, model::CpuFeatures, std::function<operators::Result()>, Next);
    void service(const char*, model::CpuFeatures, model::HostCpu::Evaluate, Next,
                 std::vector<runtime::Handle>);
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
    GraphSearch::Read ssd_read_;
    model::Controller* controller_;
    bool legacy_cost_;
    struct LegacyControl {
        runtime::Handle gate, service;
        model::HostCpu::Evaluate evaluate;
    };
    std::shared_ptr<LegacyControl> legacy_control_;
    Observe observe_;
    GraphSearch::Trace trace_;
    GraphSearch::Done done_;
    Fail fail_;
    std::function<bool()> cancelled_;
    std::string op_;
    uint64_t position_, end_, local_ = 0, version_ = 0;
    uint64_t task_ = 0;
    size_t offset_ = 0, adjacency_ = 0;
    std::vector<uint32_t> fresh_, chosen_, group_;
    std::set<uint32_t> scored_, expanded_;
    std::vector<Bytes> vectors_;
    std::vector<std::vector<uint32_t>> neighbors_;
    Work score_work_;
    Bytes returned_scores_;
    std::vector<Bytes> returned_adjacency_;
};
} // namespace ndpsim::search
