#pragma once
#include "backend/event_bridge.h"
#include "observation/history.h"
#include <functional>
#include <deque>
#include <map>
#include <vector>

namespace ndpsim::timing {
uint64_t checked_add(uint64_t, uint64_t);
struct ServiceRecord {
    uint64_t id, submitted, start, end, duration_ns;
    std::string key;
};
// Callbacks and owners must outlive outstanding bridge work. No private clock.
class FifoResource {
public:
    FifoResource(EventBridge&, EventBridge::Clock, uint64_t slots);
    uint64_t submit(std::string key, uint64_t duration_ns,
                    std::function<void()> begin, std::function<void(bool)> done);
    // Only queued jobs can be cancelled. Running services drain at their real end.
    bool cancel(uint64_t id);
    size_t pending() const { return queue_.size() + busy_; }
    uint64_t busy() const { return busy_; }
    uint64_t peak_busy() const { return peak_; }
    const std::vector<ServiceRecord>& records() const { return records_; }
    History<ServiceRecord>& observations() { return records_; }
private:
    struct Job {
        uint64_t id, submitted, duration;
        std::string key;
        std::function<void()> begin;
        std::function<void(bool)> done;
    };
    EventBridge& bridge_;
    EventBridge::Clock now_;
    uint64_t slots_, busy_ = 0, peak_ = 0, next_ = 0;
    std::deque<Job> queue_;
    History<ServiceRecord> records_;
    bool armed_ = false;
    void arm();
    void pump();
};
// Same FIFO service owner for explicitly estimated Host CPU stage demands.
class HostCpu : public FifoResource {
public:
    using FifoResource::FifoResource;
};
struct CreditRecord {
    uint64_t time, id, bytes, used;
    bool reserve;
    std::string lifetime;
};
class BufferModel {
public:
    BufferModel(EventBridge&, EventBridge::Clock, uint64_t capacity);
    uint64_t reserve(uint64_t bytes, std::string lifetime, std::function<void(uint64_t)>);
    // Queued cancellation delivers no grant. Caller owns cancellation bookkeeping.
    bool cancel(uint64_t id);
    void pin(uint64_t id);
    void unpin(uint64_t id);
    void release(uint64_t id); // Marks owner released; pinned credit remains charged.
    uint64_t used() const { return used_; }
    uint64_t peak() const { return peak_; }
    size_t waiting() const { return queue_.size(); }
    size_t live() const { return live_.size(); }
    const std::vector<CreditRecord>& records() const { return records_; }
    History<CreditRecord>& observations() { return records_; }
private:
    struct Credit {
        uint64_t id, bytes, pins = 0;
        std::string lifetime;
        bool owner = true, delivered = false;
        std::function<void(uint64_t)> grant;
    };
    EventBridge& bridge_;
    EventBridge::Clock now_;
    uint64_t capacity_, used_ = 0, peak_ = 0, next_ = 0;
    std::deque<Credit> queue_;
    std::map<uint64_t, Credit> live_;
    History<CreditRecord> records_;
    bool armed_ = false;
    void arm();
    void pump();
    void collect(uint64_t);
};
} // namespace ndpsim::timing
