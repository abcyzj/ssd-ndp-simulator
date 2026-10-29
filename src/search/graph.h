#pragma once
#include "model/host_cpu.h"
#include <set>
namespace ndpsim::search {
struct Config {
    uint32_t count = 2048, entry = 0, k = 10, width = 32, batch = 1, budget = 64, chunk = 16;
};
void validate(const Config&);
content::Bytes ids_bytes(const std::vector<uint32_t>&);
std::vector<uint32_t> ids(const content::Bytes&);
struct Decision {
    uint64_t time;
    std::vector<operators::Score> candidates;
    std::vector<uint32_t> expand;
};
// Algorithms receive a read capability, never ContentStore or backend pointers.
class GraphSearch {
public:
    using Read = std::function<runtime::Handle(content::Kind, uint32_t)>;
    using Score = std::function<runtime::Handle(const std::vector<uint32_t>&)>;
    using Trace = std::function<void(const Decision&)>;
    using Done = std::function<void(std::vector<operators::Score>, uint32_t)>;
    GraphSearch(runtime::Runtime&,
                model::HostCpu&,
                EventBridge::Clock,
                uint64_t,
                Config,
                content::Bytes,
                Read,
                Trace,
                Done,
                Score = {});
    void start();

private:
    runtime::Runtime& rt_;
    model::HostCpu& cpu_;
    EventBridge::Clock now_;
    uint64_t query_;
    Config config_;
    content::Bytes query_bytes_, all_;
    Read read_;
    Score score_;
    Trace trace_;
    Done done_;
    std::set<uint32_t> scored_, expanded_;
    void score(std::vector<uint32_t>);
    void select();
    void neighbors(std::vector<uint32_t>);
    void await(std::vector<runtime::Handle>, std::function<void(std::vector<content::Bytes>)>);
};
} // namespace ndpsim::search
