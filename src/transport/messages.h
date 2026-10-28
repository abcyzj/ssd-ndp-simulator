#pragma once
#include <cstdint>
#include <vector>
#include <stdexcept>

namespace ndpsim::transport {
using Bytes = std::vector<uint8_t>;
enum class Kind : uint16_t { QueryOpen=1, QueryOpenAck, Score, ScoreResult, QueryClose, QueryCloseAck, Error };
enum class Direction { H2D, D2H };
struct ScoreRecord { uint32_t id; double distance; };
struct Message {
    Kind kind=Kind::QueryOpen;
    uint64_t query=0, task=0, token=0, version=0, chunk=0;
    uint32_t status=0, dimension=0;
    std::vector<float> query_values;
    std::vector<uint32_t> ids;
    std::vector<ScoreRecord> scores;
};
struct ProtocolError : std::invalid_argument { using std::invalid_argument::invalid_argument; };
Direction direction(Kind);
uint64_t useful_bytes(const Message&);
Bytes encode(const Message&);
Message decode(const Bytes&, Direction);
}
