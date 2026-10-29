#include "transport/messages.h"
#include <cmath>
#include <cstring>
#include <limits>
#include <set>

namespace ndpsim::transport {
namespace {
void require(bool ok, const char* detail) {
    if (!ok)
        throw ProtocolError(detail);
}
void put(Bytes& bytes, uint64_t value, unsigned n) {
    for (unsigned i = 0; i < n; ++i)
        bytes.push_back(uint8_t(value >> (8 * i)));
}
uint64_t get(const Bytes& bytes, size_t offset, unsigned n) {
    require(offset <= bytes.size() && n <= bytes.size() - offset, "truncated message");
    uint64_t value = 0;
    for (unsigned i = 0; i < n; ++i)
        value |= uint64_t(bytes[offset + i]) << (8 * i);
    return value;
}
template <class T> uint64_t bits(T value) {
    static_assert(std::numeric_limits<T>::is_iec559);
    if constexpr (sizeof(T) == 4) {
        uint32_t v;
        std::memcpy(&v, &value, 4);
        return v;
    } else {
        uint64_t v;
        std::memcpy(&v, &value, 8);
        return v;
    }
}
template <class T> T real(uint64_t value) {
    T result;
    if constexpr (sizeof(T) == 4) {
        auto v = uint32_t(value);
        std::memcpy(&result, &v, 4);
    } else
        std::memcpy(&result, &value, 8);
    require(std::isfinite(result), "nonfinite message value");
    return result;
}
} // namespace
Direction direction(Kind kind) {
    switch (kind) {
    case Kind::QueryOpen:
    case Kind::Score:
    case Kind::QueryClose:
        return Direction::H2D;
    case Kind::QueryOpenAck:
    case Kind::ScoreResult:
    case Kind::QueryCloseAck:
    case Kind::Error:
        return Direction::D2H;
    }
    throw ProtocolError("unknown message kind");
}
uint64_t useful_bytes(const Message& m) {
    switch (m.kind) {
    case Kind::QueryOpen:
        return 4ULL * m.query_values.size();
    case Kind::Score:
        return 4ULL * m.ids.size();
    case Kind::ScoreResult:
        return 12ULL * m.scores.size();
    default:
        return 0;
    }
}
Message decode(const Bytes& bytes, Direction destination) {
    require(bytes.size() >= 48 && bytes.size() <= INT32_MAX, "invalid envelope length");
    require(get(bytes, 0, 4) == 0x5350444e, "invalid message magic");
    require(get(bytes, 4, 2) == 1, "unsupported schema version");
    Message m;
    m.kind = Kind(get(bytes, 6, 2));
    require(direction(m.kind) == destination, "invalid message direction");
    require(get(bytes, 8, 4) == bytes.size(), "total_bytes mismatch");
    m.status = get(bytes, 12, 4);
    m.query = get(bytes, 16, 8);
    m.task = get(bytes, 24, 8);
    m.token = get(bytes, 32, 8);
    auto count = get(bytes, 40, 4);
    require(get(bytes, 44, 4) == 0, "nonzero header reserved field");
    require(m.query && m.task, "zero query/task identity");
    require(m.status <= 6 && (m.kind == Kind::Error ? m.status != 0 : m.status == 0),
            "invalid status");
    require(m.kind == Kind::QueryOpen ? m.token == 0 : (m.kind == Kind::Error || m.token != 0),
            "invalid query token");
    switch (m.kind) {
    case Kind::QueryOpen:
        require(count && bytes.size() == 64 + 4 * count, "invalid query size/count");
        m.dimension = get(bytes, 48, 4);
        require(m.dimension == count && get(bytes, 52, 2) == 1 && get(bytes, 54, 2) == 1,
                "unsupported query dimension/metric/dtype");
        m.version = get(bytes, 56, 8);
        require(m.version != 0, "zero content version");
        for (uint64_t i = 0; i < count; ++i)
            m.query_values.push_back(real<float>(get(bytes, 64 + 4 * i, 4)));
        break;
    case Kind::Score:
        require(count && bytes.size() == 64 + 4 * count, "invalid score size/count");
        m.chunk = get(bytes, 48, 8);
        m.dimension = get(bytes, 56, 4);
        require(m.dimension && get(bytes, 60, 4) == 0, "invalid score dimension/reserved");
        for (uint64_t i = 0; i < count; ++i)
            m.ids.push_back(get(bytes, 64 + 4 * i, 4));
        require(std::set<uint32_t>(m.ids.begin(), m.ids.end()).size() == count,
                "duplicate score IDs");
        break;
    case Kind::ScoreResult: {
        require(count && bytes.size() == 56 + 12 * count, "invalid result size/count");
        m.chunk = get(bytes, 48, 8);
        std::set<uint32_t> seen;
        for (uint64_t i = 0; i < count; ++i) {
            auto id = uint32_t(get(bytes, 56 + 12 * i, 4));
            require(seen.insert(id).second, "duplicate result IDs");
            m.scores.push_back({id, real<double>(get(bytes, 60 + 12 * i, 8))});
        }
        break;
    }
    default:
        require(!count && bytes.size() == 48, "invalid control size/count");
    }
    return m;
}
Bytes encode(const Message& m) {
    auto dir = direction(m.kind);
    Bytes body;
    uint64_t count = 0;
    switch (m.kind) {
    case Kind::QueryOpen:
        count = m.query_values.size();
        put(body, m.dimension, 4);
        put(body, 1, 2);
        put(body, 1, 2);
        put(body, m.version, 8);
        for (auto value : m.query_values)
            put(body, bits(value), 4);
        break;
    case Kind::Score:
        count = m.ids.size();
        put(body, m.chunk, 8);
        put(body, m.dimension, 4);
        put(body, 0, 4);
        for (auto id : m.ids)
            put(body, id, 4);
        break;
    case Kind::ScoreResult:
        count = m.scores.size();
        put(body, m.chunk, 8);
        for (auto record : m.scores) {
            put(body, record.id, 4);
            put(body, bits(record.distance), 8);
        }
        break;
    default:
        break;
    }
    require(body.size() <= INT32_MAX - 48 && count <= UINT32_MAX, "message length overflow");
    Bytes out;
    put(out, 0x5350444e, 4);
    put(out, 1, 2);
    put(out, uint16_t(m.kind), 2);
    put(out, 48 + body.size(), 4);
    put(out, m.status, 4);
    put(out, m.query, 8);
    put(out, m.task, 8);
    put(out, m.token, 8);
    put(out, count, 4);
    put(out, 0, 4);
    out.insert(out.end(), body.begin(), body.end());
    decode(out, dir);
    return out;
}
} // namespace ndpsim::transport
