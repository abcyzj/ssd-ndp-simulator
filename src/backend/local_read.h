#pragma once
#include "backend/event_bridge.h"
#include "content/content.h"
#include "ssd/Host_Interface_NVMe.h"
#include <set>
#include <algorithm>
#include <sstream>

namespace ndpsim {
// Storage service only. The caller must reserve credit before submit, then stage
// input before publishing a DeviceBuffer handle (M3.3). No content is published here.
// Owns the local completion queue exclusively; outlive outstanding bridge callbacks.
class LocalReads {
public:
    using Request = SSD_Components::Host_Interface_NVMe::LocalReadCompletion;
    struct Completion {
        uint64_t operation;
        content::Context context;
        content::Plan plan;
        std::vector<Request> requests;
    };
    // Optional research observer; cannot influence the algorithm or storage charge.
    std::function<void(const Completion&)> observe;
    using Done = std::function<void(Completion)>;
    LocalReads(SSD_Components::Host_Interface_NVMe& host,
               EventBridge& bridge,
               EventBridge::Clock now)
        : host_(host), bridge_(bridge), now_(std::move(now)) {
    }
    LocalReads(const LocalReads&) = delete;
    LocalReads& operator=(const LocalReads&) = delete;
    uint64_t submit(const content::Layout& layout,
                    std::vector<content::Selection> selections,
                    content::Context context,
                    Done done) {
        context.time_ns = now_();
        if (poisoned_)
            throw content::Error(
                content::Code::BackendFailure, "local adapter already failed", context);
        if (!context.query || !context.task || !done)
            throw content::Error(
                content::Code::InvalidArgument, "local read requires query/task/callback", context);
        content::ReadAssociation association(layout,
                                             std::move(selections),
                                             content::Route::Local,
                                             content::Location::DeviceBuffer,
                                             context);
        if (association.plan().extents.empty())
            throw content::Error(content::Code::InvalidArgument, "empty local read", context);
        // Validate the entire plan before issuing its first request.
        for (const auto& e : association.plan().extents) {
            try {
                host_.Validate_local_read(0, e.lba, e.sectors);
            } catch (const std::invalid_argument& e) {
                auto code = std::string(e.what()).find("Unsupported") != std::string::npos
                                ? content::Code::Unsupported
                                : content::Code::InvalidArgument;
                throw content::Error(code, e.what(), context);
            }
        }
        if (next_ == UINT64_MAX)
            throw content::Error(content::Code::Overflow, "local operation ID exhausted", context);
        auto id = next_++;
        auto& op =
            active_
                .emplace(id, Operation{context, std::move(association), {}, std::move(done), false})
                .first->second;
        try {
            Simulator->Synchronize_now(now_()); // No stepping, no crossing SST now.
            for (size_t i = 0; i < op.association.plan().extents.size(); ++i) {
                const auto& e = op.association.plan().extents[i];
                auto token = host_.Submit_local_read(0, e.lba, e.sectors);
                op.association.bind(i, token);
                auto snapshot = host_.Inspect_local_read(token);
                bound_.emplace(token, Binding{id, std::move(snapshot)});
            }
        } catch (const std::exception& e) {
            // After injection cache/FTL/PHY may own transactions. Never fake a
            // successful completion or delete in-flight objects: abort the run.
            poisoned_ = true;
            throw content::Error(content::Code::BackendFailure,
                                 "fatal local submit operation=" + std::to_string(id) + ": " +
                                     e.what(),
                                 context);
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
            throw content::Error(content::Code::BackendFailure,
                                 "local adapter already failed",
                                 {0, 0, now_()},
                                 0,
                                 c.token);
        auto binding = bound_.find(c.token);
        if (binding == bound_.end()) {
            poisoned_ = true;
            auto prior = retired_.find(c.token);
            auto context = prior == retired_.end() ? content::Context{} : prior->second;
            context.time_ns = now_();
            throw content::Error(content::Code::BackendFailure,
                                 "unknown/duplicate local completion",
                                 context,
                                 0,
                                 c.token);
        }
        auto id = binding->second.operation;
        auto& op = active_.at(id);
        auto& expected = binding->second.request;
        auto fatal = [&](const std::string& detail) {
            poisoned_ = true;
            auto context = op.context;
            context.time_ns = now_();
            throw content::Error(content::Code::BackendFailure,
                                 "fatal local completion operation=" + std::to_string(id) + ": " +
                                     detail,
                                 context,
                                 0,
                                 c.token);
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
            total += tr.bytes;
        }
        if (total != c.bytes)
            fatal("transaction bytes mismatch");
        op.association.complete(c.token, c.lba, c.sectors, false);
        retired_.emplace(c.token, op.context);
        bound_.erase(binding);
        op.requests.push_back(std::move(c));
        if (op.association.complete()) {
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
                                    id, op.context, op.association.plan(), std::move(op.requests)};
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
                << " task=" << p.second.context.task;
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
        content::Context context;
        content::ReadAssociation association;
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
    // Value-only tombstones diagnose duplicates after request/parent reclamation.
    std::map<uint64_t, content::Context> retired_;
    std::map<uint64_t, Operation> active_;
    std::map<uint64_t, Binding> bound_;
};
} // namespace ndpsim
