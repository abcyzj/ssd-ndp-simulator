#pragma once
#include "backend/event_bridge.h"
#include "layout/layout.h"
#include "ssd/Host_Interface_NVMe.h"
#include <set>
#include <algorithm>
#include <sstream>

namespace ndpsim::timing {
// Metadata-only local storage. Reserve credit before submission; stage before compute.
// Uses the existing MQSim local completion and transaction validation contract.
// Owns the local completion queue exclusively; outlive outstanding bridge callbacks.
class MetadataLocalReads {
public:
    using Request = SSD_Components::Host_Interface_NVMe::LocalReadCompletion;
    struct Completion {
        uint64_t operation;
        capture::Key context;
        layout::Plan plan;
        std::vector<Request> requests;
    };
    // Optional research observer; cannot influence the algorithm or storage charge.
    std::function<void(const Completion&)> observe;
    using Done = std::function<void(Completion)>;
    MetadataLocalReads(SSD_Components::Host_Interface_NVMe& host,
                       EventBridge& bridge,
                       EventBridge::Clock now)
        : host_(host), bridge_(bridge), now_(std::move(now)) {
    }
    MetadataLocalReads(const MetadataLocalReads&) = delete;
    MetadataLocalReads& operator=(const MetadataLocalReads&) = delete;
    void validate(const layout::Plan& plan) const {
        if (plan.reads.empty())
            throw std::invalid_argument("empty local read");
        uint64_t bytes = 0;
        for (const auto& r : plan.reads) {
            if (!r.sectors || r.sectors > UINT32_MAX)
                throw std::invalid_argument("local sector count");
            host_.Validate_local_read(0, r.lba, r.sectors);
            if (r.sectors > (UINT64_MAX - bytes) / 512)
                throw std::overflow_error("local bytes");
            bytes += r.sectors * 512;
        }
        if (bytes != plan.sector_bytes)
            throw std::invalid_argument("local plan byte mismatch");
    }
    uint64_t submit(layout::Plan plan, capture::Key context, Done done) {
        if (poisoned_)
            throw std::runtime_error("local adapter already failed");
        if (context.op.empty() || !done)
            throw std::invalid_argument("local read identity/callback");
        validate(plan); // Entire plan before injection, including its last extent.
        if (next_ == UINT64_MAX)
            throw std::overflow_error("local operation ID exhausted");
        auto id = next_++;
        auto& op =
            active_.emplace(id, Operation{context, std::move(plan), {}, {}, std::move(done), false})
                .first->second;
        try {
            Simulator->Synchronize_now(now_()); // No stepping, no crossing SST now.
            for (size_t i = 0; i < op.plan.reads.size(); ++i) {
                const auto& e = op.plan.reads[i];
                auto token = host_.Submit_local_read(0, e.lba, e.sectors);
                op.remaining.insert(token);
                auto snapshot = host_.Inspect_local_read(token);
                bound_.emplace(token, Binding{id, std::move(snapshot)});
            }
        } catch (const std::exception& e) {
            // After injection cache/FTL/PHY may own transactions. Never fake a
            // successful completion or delete in-flight objects: abort the run.
            poisoned_ = true;
            throw std::runtime_error("fatal local submit operation=" + std::to_string(id) + ": " +
                                     e.what());
        }
        return id;
    }
    // Call after each backend time slice has drained, including safe cleanup.
    void poll() {
        for (auto c : host_.Take_local_completions())
            accept(std::move(c));
    }
    // Value boundary also usable by deterministic fault/ordering tests.
    void accept(Request c) {
        if (poisoned_)
            throw std::runtime_error("local adapter already failed");
        auto binding = bound_.find(c.token);
        if (binding == bound_.end()) {
            poisoned_ = true;
            throw std::runtime_error("unknown/duplicate local completion");
        }
        auto id = binding->second.operation;
        auto& op = active_.at(id);
        auto& expected = binding->second.request;
        auto fatal = [&](const std::string& detail) {
            poisoned_ = true;
            throw std::runtime_error("fatal local completion operation=" + std::to_string(id) +
                                     ": " + detail);
        };
        if (c.request_id != expected.request_id || c.lba != expected.lba ||
            c.sectors != expected.sectors || c.bytes != expected.bytes ||
            c.stream != expected.stream || c.submitted != expected.submitted || c.time > now_() ||
            c.time < c.submitted || c.transactions.size() != expected.transactions.size())
            fatal("request metadata/time mismatch");
        uint64_t total = 0;
        std::set<uint64_t> seen;
        for (const auto& tr : c.transactions) {
            auto it = std::find_if(
                expected.transactions.begin(), expected.transactions.end(), [&](const auto& t) {
                    return t.id == tr.id;
                });
            if (it == expected.transactions.end() || !seen.insert(tr.id).second || !tr.completed ||
                tr.lpa != it->lpa || tr.bitmap != it->bitmap || tr.bytes != it->bytes ||
                tr.time < c.submitted || tr.time > c.time)
                fatal("transaction metadata/completion mismatch");
            if (tr.bytes > UINT64_MAX - total)
                fatal("transaction bytes overflow");
            total += tr.bytes;
        }
        if (total != c.bytes)
            fatal("transaction bytes mismatch");
        if (!op.remaining.erase(c.token))
            fatal("duplicate token");
        bound_.erase(binding);
        op.requests.push_back(std::move(c));
        if (op.remaining.empty()) {
            if (op.queued)
                fatal("duplicate parent completion");
            op.queued = true;
            bridge_.enqueue(now_(),
                            EventBridge::ResourceCompleteRelease,
                            "local read=" + std::to_string(id),
                            [this, id] {
                                if (poisoned_)
                                    return; // A fatal experiment never emits a later success.
                                auto it = active_.find(id);
                                auto op = std::move(it->second);
                                active_.erase(it);
                                // Remove all adapter ownership before notifying the storage consumer.
                                Completion completed{
                                    id, op.context, op.plan, std::move(op.requests)};
                                if (observe)
                                    observe(completed);
                                op.done(std::move(completed));
                            });
        }
    }
    // Research/fatal diagnostics, never an algorithm-visible storage view.
    std::string diagnostic() const {
        std::ostringstream out;
        out << " local_failed=" << poisoned_;
        for (const auto& p : active_)
            out << " operation=" << p.first << " query=" << p.second.context.query
                << " task=" << p.second.context.op;
        for (const auto& p : bound_) {
            out << " request=" << p.first << " native_request=" << p.second.request.request_id
                << " operation=" << p.second.operation << " transactions=[";
            for (const auto& tr : p.second.request.transactions)
                out << tr.id << ",";
            out << "]";
        }
        return out.str();
    }
    size_t pending() const {
        return active_.size();
    }
    size_t pending_requests() const {
        return bound_.size();
    }

private:
    struct Operation {
        capture::Key context;
        layout::Plan plan;
        std::set<uint64_t> remaining;
        std::vector<Request> requests;
        Done done;
        bool queued;
    };
    struct Binding {
        uint64_t operation;
        Request request;
    };
    SSD_Components::Host_Interface_NVMe& host_;
    EventBridge& bridge_;
    EventBridge::Clock now_;
    uint64_t next_ = 1;
    bool poisoned_ = false;
    std::map<uint64_t, Operation> active_;
    std::map<uint64_t, Binding> bound_;
};
} // namespace ndpsim::timing
