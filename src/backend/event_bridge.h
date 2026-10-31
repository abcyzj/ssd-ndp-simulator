#pragma once
#include "sim/Engine.h"
#include <map>
#include <deque>
#include <tuple>
#include <string>
#include <limits>
#include <sstream>

namespace ndpsim {
// Single engine, single SST rank/thread. The owner supplies SST now and SelfLink.
class EventBridge {
public:
    enum Phase { Backend, ResourceCompleteRelease, Publish, ContinueSubmit };
    using Clock = std::function<uint64_t()>;
    using Reserve = std::function<void(uint64_t, uint64_t)>;
    EventBridge(Clock clock, Reserve reserve, size_t limit = 100000)
        : clock_(std::move(clock)), reserve_(std::move(reserve)), limit_(limit) {
        if (!limit_)
            throw std::invalid_argument("bridge limit must be positive");
    }
    EventBridge(const EventBridge&) = delete;
    EventBridge& operator=(const EventBridge&) = delete;
    ~EventBridge() {
        Simulator->Set_event_queue_changed_callback({});
    }
    void start() {
        Simulator->Set_event_queue_changed_callback([this] {
            if (!running_)
                arm();
        });
        arm();
    }
    void enqueue(uint64_t time, Phase phase, std::string cause, std::function<void()> action) {
        if (time < clock_())
            fail("past platform insertion");
        uint64_t delta = 0;
        if (running_ && time == clock_())
            delta = delta_ + (phase < phase_);
        if (sequence_ == UINT64_MAX || delta == UINT64_MAX)
            fail("counter overflow");
        queue_.emplace(Key{time, delta, phase, sequence_++},
                       Item{std::move(cause), std::move(action)});
        if (!running_)
            arm();
    }
    bool wake(uint64_t generation) {
        if (generation != generation_)
            return false; // Never synchronize on a stale wake.
        reserved_.reset();
        auto now = clock_();
        if (!last_ || *last_ != now) {
            last_ = now;
            backend_count_ = platform_count_ = 0;
        }
        running_ = true;
        delta_ = 0;
        phase_ = Backend;
        try {
            for (;;) {
                if (!queue_.empty() && std::get<0>(queue_.begin()->first) == now)
                    delta_ = std::get<1>(queue_.begin()->first);
                phase_ = Backend;
                auto dispatched = Simulator->Dispatch_current_time(now, limit_ - backend_count_);
                backend_count_ += dispatched;
                auto remaining = Simulator->Peek_next_time();
                if (remaining && *remaining == now)
                    fail("backend stopped with pending events");
                auto it = queue_.begin();
                if (it == queue_.end() || std::get<0>(it->first) != now)
                    break;
                delta_ = std::get<1>(it->first);
                phase_ = std::get<2>(it->first);
                if (++platform_count_ > limit_)
                    fail("platform same-time limit");
                auto item = std::move(it->second);
                queue_.erase(it);
                history_.push_back(item.cause);
                if (history_.size() > 8)
                    history_.pop_front();
                item.action();
                // Backward-phase backend work belongs to the next delta.
                auto next = Simulator->Peek_next_time();
                if (next && *next == now && phase_ != Backend) {
                    enqueue(now, Backend, "backend reentry", [] {});
                    // Finish the current delta before dispatching backend work.
                    while (!queue_.empty() && std::get<0>(queue_.begin()->first) == now &&
                           std::get<1>(queue_.begin()->first) == delta_) {
                        auto current = queue_.begin();
                        phase_ = std::get<2>(current->first);
                        if (++platform_count_ > limit_)
                            fail("platform same-time limit");
                        auto work = std::move(current->second);
                        queue_.erase(current);
                        history_.push_back(work.cause);
                        if (history_.size() > 8)
                            history_.pop_front();
                        work.action();
                    }
                }
            }
        } catch (const std::exception& e) {
            running_ = false;
            if (std::string(e.what()).rfind("Stalled:", 0) == 0)
                throw;
            fail(e.what());
        }
        running_ = false;
        arm();
        return true;
    }
    void require_progress(size_t unfinished, const std::string& detail) {
        if (unfinished && !reserved_)
            fail("empty queues with unfinished=" + std::to_string(unfinished) + " " + detail);
    }
    bool dispatching() const {
        return running_;
    }
    bool idle() const {
        return !reserved_;
    }

private:
    using Key = std::tuple<uint64_t, uint64_t, Phase, uint64_t>;
    struct Item {
        std::string cause;
        std::function<void()> action;
    };
    [[noreturn]] void fail(const std::string& reason) const {
        std::ostringstream out;
        out << "Stalled: " << reason << " SST=" << clock_() << " MQ=" << Simulator->Time()
            << " generation=" << generation_ << " delta=" << delta_ << " phase=" << phase_
            << " backend_count=" << backend_count_ << " platform_count=" << platform_count_;
        for (const auto& cause : history_)
            out << " <- " << cause;
        throw std::logic_error(out.str());
    }
    void arm() {
        auto next = Simulator->Peek_next_time();
        if (!queue_.empty() && (!next || std::get<0>(queue_.begin()->first) < *next))
            next = std::get<0>(queue_.begin()->first);
        if (next == reserved_)
            return;
        if (generation_ == UINT64_MAX)
            fail("generation overflow");
        reserved_ = next;
        ++generation_;
        if (next) {
            if (*next < clock_())
                fail("past reservation");
            reserve_(*next - clock_(), generation_);
        }
    }
    Clock clock_;
    Reserve reserve_;
    size_t limit_;
    std::map<Key, Item> queue_;
    std::deque<std::string> history_;
    std::optional<uint64_t> reserved_, last_;
    uint64_t generation_ = 0, sequence_ = 0, delta_ = 0;
    Phase phase_ = Backend;
    bool running_ = false;
    size_t backend_count_ = 0, platform_count_ = 0;
};
} // namespace ndpsim
