#pragma once
#include "timing/resources.h"
#include "observation/ids.h"
#include "host/PCIe_Link.h"
#include <set>
#include <memory>
namespace ndpsim::timing {
enum class MessageKind { Open, OpenAck, Execute, Result, Close, CloseAck, Error };
enum class Direction { H2D, D2H };
struct Message {
    std::string protocol = "graph-readonly/1";
    MessageKind kind = MessageKind::Open;
    uint64_t query = 0, task = 0, token = 0, version = 0, chunk = 0;
    uint64_t count = 0, dimension = 0, status = 0;
};
struct MessageSize {
    uint64_t logical, payload;
};
Direction direction(MessageKind);
class MessageRules {
public:
    using Rule = std::function<MessageSize(const Message&)>;
    MessageRules();
    void add(std::string, Rule);
    MessageSize size(const Message&) const;

private:
    std::map<std::string, Rule> rules_;
};
// Arrival-only protocol ledger. No payload arrays or content handles.
class Conversation {
public:
    void arrive(const Message&);
    size_t pending() const {
        return pending_.size();
    }
    size_t sessions() const {
        return sessions_.size();
    }
    size_t task_records() const {
        return tasks_.size();
    }

private:
    struct Session {
        uint64_t token, version, dimension;
        std::string protocol;
        bool closing = false;
        std::set<uint64_t> chunks;
    };
    std::map<uint64_t, Session> sessions_;
    std::map<std::pair<uint64_t, uint64_t>, Message> pending_;
    std::set<std::pair<uint64_t, uint64_t>> tasks_;
    CompletedIds retired_, tokens_;
};
class MetadataPcie {
public:
    struct Record {
        uint64_t id, query, task, logical, payload, wire, submitted, arrived;
        MessageKind kind;
        Direction direction;
        uint64_t chunk, count, dimension;
    };
    MetadataPcie(Host_Components::PCIe_Link&,
                 EventBridge&,
                 EventBridge::Clock,
                 const MessageRules&);
    ~MetadataPcie() {
        *alive_ = false;
    }
    MetadataPcie(const MetadataPcie&) = delete;
    uint64_t send(Message, std::function<void(Message)>);
    size_t pending() const {
        return pending_;
    }
    const std::vector<Record>& records() const {
        return records_;
    }
    History<Record>& observations() {
        return records_;
    }

private:
    Host_Components::PCIe_Link& link_;
    EventBridge& bridge_;
    EventBridge::Clock now_;
    const MessageRules& rules_;
    std::shared_ptr<bool> alive_ = std::make_shared<bool>(true);
    uint64_t next_ = 0, pending_ = 0;
    History<Record> records_;
};
} // namespace ndpsim::timing
