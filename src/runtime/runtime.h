#pragma once
#include "backend/event_bridge.h"
#include "content/content.h"
#include <memory>
#include <optional>

namespace ndpsim::runtime {
using content::Bytes;
using content::Code;
using content::Location;
enum class State { Submitted, WaitingDependencies, WaitingResource, Running, ServiceDone, WaitingReturn, Visible, Failed };
const char* name(State);
struct Failure {
    Code code; uint64_t query, task, handle, time_ns, causal_parent;
    std::string detail;
};
struct Outcome { std::optional<Failure> failure; };
struct Record;
class Runtime;
class Handle {
public:
    Handle()=default;
    uint64_t id() const;
private:
    std::shared_ptr<Record> record_;
    std::shared_ptr<bool> active_;
    friend class Runtime;
};
// Trusted adapters own these callbacks. Algorithms only receive View and Handle.
// grant() calls start(); service/return adapters signal their actual completion.
struct Operation {
    uint64_t query=0;
    Location location=Location::HostMemory;
    bool needs_return=false;
    content::Consumer executor=content::Consumer::HostExecutor;
    std::vector<Handle> inputs;
    std::function<void(uint64_t)> grant;
    std::function<Bytes(const std::vector<Bytes>&)> evaluate;
    std::function<void(uint64_t)> service;
    std::function<void(uint64_t,size_t)> send_return;
};
struct Transition { uint64_t time_ns, query, task; State state; };
class Runtime {
public:
    Runtime(EventBridge& bridge, std::function<uint64_t()> now):bridge_(bridge),now_(std::move(now)) {}
    Runtime(const Runtime&)=delete;
    Runtime& operator=(const Runtime&)=delete;
    Handle submit(Operation);
    Handle retain(Handle);
    void release(Handle);
    Bytes try_get(Handle, content::Consumer=content::Consumer::Algorithm) const;
    void when_all(std::vector<Handle>, uint64_t continuation, std::function<void(Outcome)>);
    void start(uint64_t task);
    void service_done(uint64_t task);
    void return_done(uint64_t task);
    void fail(uint64_t task, Code, std::string detail);
    void require_progress() const;
    std::string diagnostic() const;
    size_t live_results() const;
    size_t private_slots() const;
    State state(uint64_t task) const;
    const std::vector<Transition>& transitions() const { return transitions_; }
private:
    struct Task { Operation op; std::shared_ptr<Record> result; State state=State::Submitted; uint64_t wait=0; bool return_arrived=false; };
    struct Wait { uint64_t id; std::vector<std::shared_ptr<Record>> inputs; std::function<void(Outcome)> callback; };
    EventBridge& bridge_; std::function<uint64_t()> now_;
    std::map<uint64_t,Task> tasks_;
    std::map<uint64_t,Wait> waits_;
    uint64_t next_wait_=1;
    std::vector<Transition> transitions_;
    std::shared_ptr<Record> checked(Handle) const;
    Task& task(uint64_t);
    void transition(Task&,State);
    uint64_t wait(std::vector<Handle>, uint64_t, std::function<void(Outcome)>);
    void scan();
    void publish(uint64_t);
    void failed(uint64_t,Failure);
    void collect(const std::shared_ptr<Record>&);
    void boundary(uint64_t,State,State);
};
class View {
public:
    explicit View(const Runtime& r):runtime_(r) {}
    Bytes try_get(Handle h) const { return runtime_.try_get(h); }
private:
    const Runtime& runtime_;
};
}
