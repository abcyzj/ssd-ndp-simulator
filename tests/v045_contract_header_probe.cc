#include "docs/design-v0.4.5/interfaces.h"
#include <type_traits>
using namespace ndpsim::v045;
static_assert(static_cast<int>(Phase::Backend) == 0);
static_assert(static_cast<int>(Phase::Release) == 1);
static_assert(static_cast<int>(Phase::Publish) == 2);
static_assert(static_cast<int>(Phase::Continue) == 3);
static_assert(std::is_abstract_v<Mapping> && std::is_abstract_v<CostModel>);
static_assert(std::is_abstract_v<CaptureWriter> && std::is_abstract_v<Runtime>);
static_assert(std::is_same_v<Ns, std::uint64_t>);
int main() {
    Stage stage{};
    stage.key = {7, "op", "stage"};
    stage.work = {{"elements", "count", 16}, {"flops", "flop", 15}};
    stage.outputs = {{"out", "token", "fixture", 0, 0}};
    TransferDescriptor message{};
    message.payload_bytes = 1048576; // Size only; no payload allocation.
    return stage.work.size() == 2 && message.payload_bytes == 1048576 ? 0 : 1;
}
