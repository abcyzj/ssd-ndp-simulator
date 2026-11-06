#include "timing/resources.h"
#include <algorithm>
#include <stdexcept>

namespace ndpsim::timing {
uint64_t checked_add(uint64_t a, uint64_t b) {
    if (b > UINT64_MAX - a) throw std::overflow_error("timing uint64 addition");
    return a + b;
}
FifoResource::FifoResource(EventBridge& b, EventBridge::Clock n, uint64_t slots)
    : bridge_(b), now_(std::move(n)), slots_(slots) {
    if (!slots_ || !now_) throw std::invalid_argument("resource slots/clock required");
}
uint64_t FifoResource::submit(std::string key, uint64_t duration,
                             std::function<void()> begin, std::function<void(bool)> done) {
    if (key.empty() || !begin || !done) throw std::invalid_argument("incomplete service demand");
    checked_add(now_(), duration);
    auto id = checked_add(next_, 1);
    queue_.push_back({id, now_(), duration, std::move(key), std::move(begin), std::move(done)});
    next_ = id;
    arm();
    return id;
}
void FifoResource::arm() {
    if (armed_) return;
    armed_ = true;
    bridge_.enqueue(now_(), EventBridge::ContinueSubmit, "metadata resource pump", [this] {
        armed_ = false;
        pump();
    });
}
void FifoResource::pump() {
    while (busy_ < slots_ && !queue_.empty()) {
        // Check again after queueing delay, before removing a job or taking a slot.
        auto end = checked_add(now_(), queue_.front().duration);
        auto j = std::move(queue_.front());
        queue_.pop_front();
        ++busy_;
        peak_ = std::max(peak_, busy_);
        records_.push_back({j.id, j.submitted, now_(), end, j.duration, j.key});
        // Grant in phase3. Completion/release always crosses the bridge, even at 0ns.
        bridge_.enqueue(end, EventBridge::ResourceCompleteRelease, "metadata service done",
                        [this, done = std::move(j.done)] {
            --busy_;
            done(true);
            arm();
        });
        j.begin(); // Adapter errors are fatal; the already-issued service still has a drain event.
    }
}
bool FifoResource::cancel(uint64_t id) {
    auto it = std::find_if(queue_.begin(), queue_.end(), [id](const Job& j) { return j.id == id; });
    if (it == queue_.end()) return false;
    auto done = std::move(it->done);
    queue_.erase(it);
    bridge_.enqueue(now_(), EventBridge::ResourceCompleteRelease, "cancel metadata demand",
                    [done = std::move(done)] { done(false); });
    return true;
}
BufferModel::BufferModel(EventBridge& b, EventBridge::Clock n, uint64_t capacity)
    : bridge_(b), now_(std::move(n)), capacity_(capacity) {
    if (!capacity_ || !now_) throw std::invalid_argument("buffer capacity/clock required");
}
uint64_t BufferModel::reserve(uint64_t bytes, std::string lifetime,
                              std::function<void(uint64_t)> grant) {
    if (!grant || lifetime.empty()) throw std::invalid_argument("incomplete credit request");
    if (bytes > capacity_) throw std::invalid_argument("credit exceeds buffer capacity");
    auto id = checked_add(next_, 1);
    queue_.push_back({id, bytes, 0, std::move(lifetime), true, false, std::move(grant)});
    next_ = id;
    arm();
    return id;
}
void BufferModel::arm() {
    if (armed_) return;
    armed_ = true;
    bridge_.enqueue(now_(), EventBridge::ContinueSubmit, "metadata credit pump", [this] {
        armed_ = false;
        pump();
    });
}
void BufferModel::pump() {
    // Strict head-of-line FIFO: a smaller later request must not bypass the head.
    while (!queue_.empty() && queue_.front().bytes <= capacity_ - used_) {
        auto c = std::move(queue_.front());
        queue_.pop_front();
        used_ += c.bytes;
        peak_ = std::max(peak_, used_);
        auto id = c.id;
        records_.push_back({now_(), id, c.bytes, used_, true, c.lifetime});
        live_.emplace(id, std::move(c));
        bridge_.enqueue(now_(), EventBridge::ContinueSubmit, "metadata credit grant", [this, id] {
            auto& credit = live_.at(id);
            credit.delivered = true;
            auto grant = std::move(credit.grant);
            grant(id);
        });
    }
}
bool BufferModel::cancel(uint64_t id) {
    auto it = std::find_if(queue_.begin(), queue_.end(), [id](const Credit& c) { return c.id == id; });
    if (it == queue_.end()) return false;
    queue_.erase(it);
    arm();
    return true;
}
void BufferModel::pin(uint64_t id) {
    auto& c = live_.at(id);
    if (!c.owner || !c.delivered) throw std::logic_error("pin of released/ungranted credit");
    c.pins = checked_add(c.pins, 1);
}
void BufferModel::unpin(uint64_t id) {
    auto& c = live_.at(id);
    if (!c.pins) throw std::logic_error("credit pin underflow");
    --c.pins;
    collect(id);
}
void BufferModel::release(uint64_t id) {
    auto& c = live_.at(id);
    if (!c.owner || !c.delivered) throw std::logic_error("duplicate/ungranted credit release");
    c.owner = false;
    collect(id);
}
void BufferModel::collect(uint64_t id) {
    const auto& c = live_.at(id);
    if (c.owner || c.pins) return;
    // Keep capacity charged until the phase1 release actually executes.
    bridge_.enqueue(now_(), EventBridge::ResourceCompleteRelease, "metadata credit release", [this, id] {
        auto it = live_.find(id);
        if (it == live_.end()) throw std::logic_error("duplicate credit collection");
        used_ -= it->second.bytes;
        records_.push_back({now_(), id, it->second.bytes, used_, false, it->second.lifetime});
        live_.erase(it);
        arm();
    });
}
} // namespace ndpsim::timing
