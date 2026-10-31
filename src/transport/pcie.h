#pragma once
#include "observation/history.h"
#include "transport/messages.h"
#include "backend/event_bridge.h"
#include "host/PCIe_Link.h"
#include <memory>

namespace ndpsim::transport {
// No buffer, compute, or endpoint bandwidth model here. Endpoint processing defaults to zero.
// Completion is deferred out of MQSim backend dispatch, even for a zero-ns envelope.
class PcieTransport {
public:
    struct Record {
        uint64_t id, query, task, payload, useful, submitted, arrived = 0;
        Kind kind;
        Direction direction;
        bool complete = false;
    };
    using Receive = std::function<void(uint64_t, Message)>;
    PcieTransport(Host_Components::PCIe_Link& link,
                  EventBridge& bridge,
                  std::function<uint64_t()> now)
        : link_(link), bridge_(bridge), now_(std::move(now)) {
    }
    PcieTransport(const PcieTransport&) = delete;
    PcieTransport& operator=(const PcieTransport&) = delete;
    ~PcieTransport() {
        *alive_ = false;
    }
    uint64_t send(const Message& message, Receive receive) {
        using namespace Host_Components;
        if (!receive)
            throw std::invalid_argument("missing PCIe endpoint");
        if (now_() != Simulator->Time())
            throw std::logic_error("PCIe injection requires synchronized SST now");
        auto bytes = encode(message);
        auto owned = std::make_unique<PCIe_Message>();
        auto* packet = owned.get();
        packet->Destination = direction(message.kind) == Direction::H2D
                                  ? PCIe_Destination_Type::DEVICE
                                  : PCIe_Destination_Type::HOST;
        packet->Type = PCIe_Message_Type::PLATFORM;
        packet->Payload = nullptr;
        packet->Address = 0;
        packet->Payload_size = bytes.size();
        if (link_.Wire_bytes(*packet) > INT32_MAX)
            throw ProtocolError("envelope exceeds native PCIe signed byte bound");
        if (next_ == UINT64_MAX)
            throw std::overflow_error("platform message ID overflow");
        auto id = ++next_;
        packet->Platform_id = id;
        inflight_.emplace(id,
                          Record{id,
                                 message.query,
                                 message.task,
                                 bytes.size(),
                                 useful_bytes(message),
                                 now_(),
                                 0,
                                 message.kind,
                                 direction(message.kind),
                                 false});
        packet->Platform_arrived = [this,
                                    alive = alive_,
                                    id,
                                    bytes = std::move(bytes),
                                    receive = std::move(receive)]() mutable {
            if (!*alive)
                return;
            auto decoded = decode(bytes, inflight_.at(id).direction);
            auto arrived = Simulator->Time();
            bridge_.enqueue(arrived,
                            EventBridge::ResourceCompleteRelease,
                            "PCIe envelope arrived",
                            [this,
                             alive,
                             id,
                             arrived,
                             decoded = std::move(decoded),
                             receive = std::move(receive)]() mutable {
                                if (!*alive)
                                    return;
                                auto& record = inflight_.at(id);
                                if (record.complete)
                                    throw std::logic_error("duplicate PCIe completion");
                                record.arrived = arrived;
                                record.complete = true;
                                records_.push_back(record);
                                receive(id, std::move(decoded));
                                inflight_.erase(id);
                            });
        };
        link_.Deliver(owned.get());
        owned.release();
        return id;
    }
    const Record& record(uint64_t id) const {
        auto it = inflight_.find(id);
        if (it != inflight_.end())
            return it->second;
        for (const auto& r : records_)
            if (r.id == id)
                return r;
        throw std::invalid_argument("invalid or reclaimed message ID");
    }
    const std::vector<Record>& records() const {
        return records_;
    }
    size_t pending() const {
        return inflight_.size();
    }

public:
    History<Record>& observations() {
        return records_;
    }

private:
    Host_Components::PCIe_Link& link_;
    EventBridge& bridge_;
    std::function<uint64_t()> now_;
    std::shared_ptr<bool> alive_ = std::make_shared<bool>(true);
    uint64_t next_ = 0;
    History<Record> records_;
    std::map<uint64_t, Record> inflight_;
};
} // namespace ndpsim::transport
