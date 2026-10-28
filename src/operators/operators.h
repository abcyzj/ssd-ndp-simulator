#pragma once
#include "content/content.h"
namespace ndpsim::operators {
// Logical bytes only; no sector padding, PCIe charge or host instruction count.
struct Work {
    uint64_t elements=0, flops=0, read_bytes=0, write_bytes=0, output_bytes=0;
    uint64_t candidate_checks=0, comparisons=0;
};
struct Result { content::Bytes bytes; Work work; };
struct Score { uint32_t id; double distance; };
content::Bytes fp32(const std::vector<float>&);
std::vector<Score> scores(const content::Bytes&); // packed LE uint32 + binary64, 12 B
Result squared_l2(const content::Bytes& query, const std::vector<content::Bytes>& vectors,
                  const std::vector<uint32_t>& ids);
// Deterministic insertion sort, retaining best score per ID, then top limit.
// Caller supplies current candidates plus new scores; visited/expanded remain caller policy.
Result candidates(const content::Bytes& packed, uint64_t limit);
}
