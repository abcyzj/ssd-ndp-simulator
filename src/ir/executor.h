#pragma once
#include "ir/schema.h"
#include "runtime/runtime.h"
#include <map>

namespace ndpsim::ir {
// Immutable query-private state; advancing a lineage never mutates an old view.
class StateSnapshot {
public:
    static std::shared_ptr<const StateSnapshot> initial(uint64_t query, std::string id, Json data);
    static std::shared_ptr<const StateSnapshot> advance(std::shared_ptr<const StateSnapshot>, Json);
    const StateRef& identity() const {
        return identity_;
    }
    const Json& data() const {
        return data_;
    }

private:
    StateRef identity_;
    Json data_;
    std::shared_ptr<uint64_t> sequence_;
};
// Immutable shared payloads: resolving an input pins the existing value block,
// rather than copying the whole candidate array. Ownership still follows leases
// and in-flight consumers; no history/cache of retired values is introduced.
struct Value {
    Value(Json data = {}, std::shared_ptr<const StateSnapshot> snapshot = {});
    static Value scores(content::Bytes); // immutable, validated packed FP64 records
    const Json& data() const; // lazy diagnostic view, same logical value/schema
    const content::Bytes* packed_scores() const;
    size_t size() const;
    std::shared_ptr<const StateSnapshot> state;
private:
    struct Payload {
        Json json;
        std::optional<content::Bytes> scores;
    };
    std::shared_ptr<Payload> data_;
};

using Values = std::map<std::string, Value>;
using States = std::map<std::string, std::shared_ptr<const StateSnapshot>>;
struct Lease {
private:
    const void* owner_ = nullptr;
    uint64_t op_ = 0;
    std::shared_ptr<bool> active_;
    friend class Executor;
};
// Trusted mapping interface, deliberately not exposed to the search frontend.
// grant/start/service/return use the original Runtime boundaries and costs.
struct Adapter {
    std::function<void(uint64_t)> grant;
    std::function<Values(const Values&)> evaluate;
    std::function<void(uint64_t)> service;
    std::function<void(uint64_t)> send_return;
    bool needs_return = false;
    // Optional trusted composite launcher, after whole-batch resolve/validation.
    std::function<void(uint64_t, const Values&)> launch;
};
struct Counts {
    uint64_t submitted = 0, completed = 0, failed = 0;
    size_t active_ops = 0, peak_ops = 0, active_values = 0, peak_values = 0;
};
class Executor {
public:
    Executor(Document contract, runtime::Runtime&, std::function<uint64_t()> now);
    Executor(const Executor&) = delete;
    Executor& operator=(const Executor&) = delete;
    // One Executor per run. The owner must drain callbacks before destroying it.
    // Every referenced producer requires a live caller lease at admission.
    // snapshots are immutable caller-owned state views pinned at submission.
    Lease submit(Operation, std::vector<Lease> producers, Adapter, States snapshots = {});
    Lease join(Operation, std::vector<Lease> producers);
    Lease retain(Lease);
    void release(Lease);
    Value get(Lease, const std::string& port) const; // HostMemory only, after Publish
    void when_all(std::vector<Lease>, uint64_t continuation, std::function<void(runtime::Outcome)>);
    // Passive streaming observer; never owns operations or values.
    std::function<void(const Operation&, const char*, uint64_t, const Values&)> observe;
    std::map<Operator, Counts> operator_counts;
    Counts counts() const {
        return counts_;
    }
    std::string diagnostic() const;
    void require_progress() const;
    // Trusted adapter only: all service/return work for this op has drained.
    void fail_after_drain(uint64_t op_id, content::Code, std::string detail);

private:
    struct Record {
        Operation op;
        runtime::Handle completion;
        size_t owners = 1, pins = 1;
        Values outputs;
        std::vector<uint64_t> producers;
        Json admission;
    };
    Document contract_;
    SubmissionValidator validator_;
    runtime::Runtime& runtime_;
    std::function<uint64_t()> now_;
    std::map<uint64_t, Record> records_;
    uint64_t last_id_ = 0;
    Counts counts_;
    const Record& checked(Lease) const;
    void collect(uint64_t);
    Values resolve(const Operation&, const States&) const;
    void validate_values(const Operation&, const Values&, const Values& inputs) const;
    [[noreturn]] void
    error(const Operation&, std::string code, std::string detail, uint64_t producer = 0) const;
};
} // namespace ndpsim::ir
