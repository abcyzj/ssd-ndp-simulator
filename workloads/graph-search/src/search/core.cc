#include "search/core.h"

namespace ndpsim::search::core {
namespace {
operators::Result ids_result(const std::vector<uint32_t>& ids, operators::Work work) {
    content::Bytes bytes;
    for (auto id : ids)
        for (unsigned j = 0; j < 4; ++j)
            bytes.push_back(uint8_t(id >> (8 * j)));
    work.write_bytes = work.output_bytes = bytes.size();
    return {std::move(bytes), work};
}
} // namespace
operators::Result select_frontier(const content::Bytes& all,
                                  const std::set<uint32_t>& expanded,
                                  uint64_t width,
                                  uint64_t batch,
                                  uint64_t budget) {
    auto ranked = operators::scores(all);
    if (ranked.size() > width)
        ranked.resize(size_t(width));
    std::vector<uint32_t> chosen;
    operators::Work w;
    for (auto s : ranked) {
        ++w.candidate_checks;
        if (expanded.size() + chosen.size() < budget && chosen.size() < batch &&
            !expanded.count(s.id))
            chosen.push_back(s.id);
    }
    return ids_result(chosen, w);
}
operators::Result dedup_neighbors(const std::vector<std::vector<uint32_t>>& rows,
                                  const std::set<uint32_t>& seen,
                                  uint64_t count) {
    // Validate the whole batch before executing the counted algorithm.
    for (const auto& row : rows)
        for (auto id : row)
            if (id >= count)
                throw content::Error(content::Code::InvalidArgument, "bad neighbor ID");
    std::vector<uint32_t> fresh;
    operators::Work w;
    for (const auto& row : rows)
        for (auto id : row) {
            ++w.candidate_checks;
            bool found = false;
            for (auto s : seen) {
                ++w.comparisons;
                if (s == id) {
                    found = true;
                    break;
                }
            }
            if (found)
                continue;
            auto it = fresh.begin();
            for (; it != fresh.end(); ++it) {
                ++w.comparisons;
                if (*it >= id)
                    break;
            }
            if (it != fresh.end())
                ++w.comparisons;
            if (it == fresh.end() || *it != id)
                fresh.insert(it, id);
        }
    return ids_result(fresh, w);
}
operators::Result merge_candidates(content::Bytes previous,
                                   const content::Bytes& batch,
                                   uint64_t limit) {
    previous.insert(previous.end(), batch.begin(), batch.end());
    return operators::candidates(previous, limit);
}
} // namespace ndpsim::search::core
