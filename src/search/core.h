#pragma once
#include "operators/operators.h"
#include <set>

namespace ndpsim::search::core {
// Pure value operations. No scheduling, reads, clocks or mutation of caller state.
// Scores must already be ranked by candidates; scan only the first width entries.
operators::Result select_frontier(const content::Bytes& ranked,
                                  const std::set<uint32_t>& expanded,
                                  uint64_t width,
                                  uint64_t batch,
                                  uint64_t budget);
// Rows contain only valid edge IDs (degree/padding removed), in expansion order.
// Scored is the immutable snapshot taken at submission, in ascending set order.
operators::Result dedup_neighbors(const std::vector<std::vector<uint32_t>>& rows,
                                  const std::set<uint32_t>& scored,
                                  uint64_t count);
// Concatenate complete batch scores in chunk order, then use existing candidates.
operators::Result merge_candidates(content::Bytes previous,
                                   const content::Bytes& batch,
                                   uint64_t limit);
} // namespace ndpsim::search::core
