#include "timing/messages.h"
namespace ndpsim::timing {
namespace {
void require(bool ok, const char* why) {
    if (!ok)
        throw std::invalid_argument(why);
}
uint64_t mul(uint64_t a, uint64_t b) {
    if (b && a > UINT64_MAX / b)
        throw std::overflow_error("message size overflow");
    return a * b;
}
} // namespace
Direction direction(MessageKind k) {
    switch (k) {
    case MessageKind::Open:
    case MessageKind::Execute:
    case MessageKind::Close:
        return Direction::H2D;
    case MessageKind::OpenAck:
    case MessageKind::Result:
    case MessageKind::CloseAck:
    case MessageKind::Error:
        return Direction::D2H;
    }
    throw std::invalid_argument("unknown message kind");
}
MessageRules::MessageRules() {
    add("graph-readonly/1", [](const Message& m) -> MessageSize {
        switch (m.kind) {
        case MessageKind::Open:
            require(m.dimension && m.dimension <= UINT32_MAX && m.count == m.dimension && !m.chunk,
                    "query dimension/count mismatch");
            return {mul(4, m.count), checked_add(64, mul(4, m.count))};
        case MessageKind::Execute:
            require(m.dimension && m.dimension <= UINT32_MAX && m.count && m.count <= UINT32_MAX &&
                        m.chunk,
                    "invalid execute dimension/count/chunk");
            return {mul(4, m.count), checked_add(64, mul(4, m.count))};
        case MessageKind::Result:
            require(m.count && m.count <= UINT32_MAX && m.chunk && !m.dimension,
                    "invalid result count/chunk");
            return {mul(12, m.count), checked_add(56, mul(12, m.count))};
        default:
            require(!m.count && !m.dimension && !m.chunk, "control has body metadata");
            return {0, 48};
        }
    });
}
void MessageRules::add(std::string id, Rule rule) {
    require(!id.empty() && bool(rule) && !rules_.count(id), "invalid/duplicate message size rule");
    rules_.emplace(std::move(id), std::move(rule));
}
MessageSize MessageRules::size(const Message& m) const {
    direction(m.kind);
    require(m.task && m.version, "missing task/version"); // Original query ID zero is valid.
    require(m.status <= 6 && (m.kind == MessageKind::Error ? m.status != 0 : m.status == 0),
            "invalid status");
    require(m.kind == MessageKind::Open ? !m.token : (m.kind == MessageKind::Error || m.token),
            "invalid token");
    auto r = rules_.find(m.protocol);
    require(r != rules_.end(), "unknown message protocol/size rule");
    auto s = r->second(m);
    require(s.logical <= s.payload && s.payload <= INT32_MAX, "invalid/oversized envelope");
    return s;
}
void Conversation::arrive(const Message& m) {
    auto key = std::make_pair(m.query, m.task);
    if (direction(m.kind) == Direction::H2D) {
        require(!tasks_.count(key), "reused command task");
        if (m.kind == MessageKind::Open) {
            require(!sessions_.count(m.query) && !retired_.contains(m.query), "query open/retired");
            sessions_.emplace(m.query, Session{0, m.version, m.dimension, m.protocol, false, {}});
        } else {
            auto s = sessions_.find(m.query);
            require(s != sessions_.end() && s->second.token && s->second.token == m.token &&
                        s->second.version == m.version && s->second.protocol == m.protocol &&
                        !s->second.closing,
                    "session identity/closing mismatch");
            if (m.kind == MessageKind::Execute) {
                require(m.dimension == s->second.dimension, "session dimension mismatch");
                require(s->second.chunks.insert(m.chunk).second, "duplicate chunk");
            } else {
                for (const auto& p : pending_)
                    require(p.first.first != m.query, "close before replies drained");
                s->second.closing = true;
            }
        }
        tasks_.insert(key);
        pending_.emplace(key, m);
        return;
    }
    auto p = pending_.find(key);
    require(p != pending_.end(), "orphan/duplicate reply");
    const auto& r = p->second;
    require(m.version == r.version && m.protocol == r.protocol, "reply version/protocol mismatch");
    auto& s = sessions_.at(m.query);
    bool retire = false;
    if (m.kind == MessageKind::Error) {
        require(m.token == r.token, "error token mismatch");
        retire = r.kind == MessageKind::Open;
        if (r.kind == MessageKind::Close)
            s.closing = false;
    } else if (r.kind == MessageKind::Open) {
        require(m.kind == MessageKind::OpenAck && m.token && !tokens_.contains(m.token),
                "invalid OpenAck");
        s.token = m.token;
        tokens_.insert(m.token);
    } else if (r.kind == MessageKind::Execute) {
        require(m.kind == MessageKind::Result && m.token == r.token && m.chunk == r.chunk &&
                    m.count == r.count,
                "result identity/count mismatch");
    } else {
        require(m.kind == MessageKind::CloseAck && m.token == r.token, "invalid CloseAck");
        retire = true;
    }
    pending_.erase(p);
    if (retire) {
        retired_.insert(m.query);
        sessions_.erase(m.query);
        tasks_.erase(tasks_.lower_bound({m.query, 0}), tasks_.upper_bound({m.query, UINT64_MAX}));
    }
}
MetadataPcie::MetadataPcie(Host_Components::PCIe_Link& link,
                           EventBridge& b,
                           EventBridge::Clock now,
                           const MessageRules& rules)
    : link_(link), bridge_(b), now_(std::move(now)), rules_(rules) {
}
uint64_t MetadataPcie::send(Message m, std::function<void(Message)> receive) {
    using namespace Host_Components;
    require(bool(receive), "missing endpoint");
    auto s = rules_.size(m);
    auto p = std::make_unique<PCIe_Message>();
    p->Destination = direction(m.kind) == Direction::H2D ? PCIe_Destination_Type::DEVICE
                                                         : PCIe_Destination_Type::HOST;
    p->Type = PCIe_Message_Type::PLATFORM;
    p->Payload = nullptr;
    p->Address = 0;
    p->Payload_size = s.payload;
    auto wire = link_.Wire_bytes(*p);
    require(wire <= INT32_MAX, "PCIe signed wire bound");
    if (next_ == UINT64_MAX)
        throw std::overflow_error("message ID overflow");
    auto id = ++next_;
    p->Platform_id = id;
    Record record{
        id, m.query, m.task, s.logical, s.payload, wire, now_(), 0, m.kind, direction(m.kind), m.chunk, m.count, m.dimension};
    p->Platform_arrived =
        [this, alive = alive_, m, record, receive = std::move(receive)]() mutable {
            if (!*alive)
                return;
            record.arrived = Simulator->Time();
            bridge_.enqueue(record.arrived,
                            EventBridge::ResourceCompleteRelease,
                            "metadata PCIe arrived",
                            [this, alive, m, record, receive = std::move(receive)]() mutable {
                                if (!*alive)
                                    return;
                                --pending_;
                                records_.push_back(record);
                                receive(m);
                            });
        };
    Simulator->Synchronize_now(now_());
    // Any backend exception is fatal: no forged completion or speculative cleanup.
    link_.Deliver(p.get());
    p.release();
    ++pending_;
    return id;
}
} // namespace ndpsim::timing
