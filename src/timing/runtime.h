#pragma once
#include "ndpsim/capture.h"
#include "timing/resources.h"
#include <memory>
#include <optional>
#include <set>

namespace ndpsim::timing {
enum class State { Submitted, WaitingDependencies, WaitingResource, Running,
                   ServiceDone, WaitingReturn, Visible, FailedDraining, Failed };
struct Failure { uint64_t source; std::string code, detail; };
struct Outcome { std::optional<Failure> failure; };
struct Record;
class CompletionToken {
public:
    uint64_t id() const;
private:
    std::shared_ptr<Record> record_;
    std::shared_ptr<bool> lease_;
    friend class Runtime;
};
// Adapters issue real input/return service and invoke Done exactly once after drain.
// A throw means no service was accepted. No payload or functional callbacks exist.
using Done = std::function<void(Outcome)>;
using Adapter = std::function<void(Done)>;
struct Operation {
    capture::Key key;
    capture::Counters work, features;
    std::vector<capture::Output> outputs;
    std::vector<CompletionToken> inputs;
    FifoResource* resource = nullptr;
    uint64_t duration_ns = 0;
    std::string service_key; // Optional stable trace identity supplied by lowering.
    Adapter input, send_return;
};
struct Transition { uint64_t time, id; State state; };
class Runtime {
public:
    Runtime(EventBridge&, EventBridge::Clock);
    Runtime(const Runtime&) = delete;
    Runtime& operator=(const Runtime&) = delete;
    CompletionToken submit(Operation);
    CompletionToken retain(CompletionToken);
    void release(CompletionToken);
    void when_complete(CompletionToken, Done);
    // Failure prevents new service. Issued input/compute/return callbacks must drain
    // before failure publication and before dependent pins are released.
    void fail(CompletionToken, std::string code, std::string detail);
    State state(CompletionToken) const;
    std::vector<capture::Output> describe(CompletionToken) const; // Visible metadata only.
    size_t task_records() const { return tasks_.size(); }
    size_t owners() const;
    size_t pins() const;
    void require_progress() const;
    const std::vector<Transition>& transitions() const { return transitions_; }
    History<Transition>& observations() { return transitions_; }
private:
    EventBridge& bridge_;
    EventBridge::Clock now_;
    uint64_t next_ = 0;
    std::map<uint64_t, std::shared_ptr<Record>> tasks_;
    History<Transition> transitions_;
    std::set<uint64_t> dependency_waiters_;
    std::shared_ptr<Record> checked(CompletionToken) const;
    void defer(const std::shared_ptr<Record>&, EventBridge::Phase, std::function<void()>);
    void transition(const std::shared_ptr<Record>&, State);
    void ready(const std::shared_ptr<Record>&);
    void compute(const std::shared_ptr<Record>&);
    void service_done(const std::shared_ptr<Record>&);
    void adapter(const std::shared_ptr<Record>&, Adapter, std::function<void()>);
    void failing(const std::shared_ptr<Record>&, Failure);
    void terminal(const std::shared_ptr<Record>&);
    void publish(const std::shared_ptr<Record>&);
    void collect(const std::shared_ptr<Record>&);
};
} // namespace ndpsim::timing
