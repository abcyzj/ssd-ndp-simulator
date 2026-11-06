#include "timing/runtime.h"
#include <cstdio>
#include <stdexcept>
#include <limits>
using namespace ndpsim;
using namespace ndpsim::timing;
void check(bool b, const char* m) { if (!b) throw std::logic_error(m); }
template<class F> void rejects(F f) {
    bool rejected = false;
    try { f(); } catch (const std::exception&) { rejected = true; }
    check(rejected, "negative case accepted");
}
// Test-only external wake driver. Production uses SST SelfLink, never this loop.
struct Fixture {
    uint64_t now = 0;
    std::multimap<uint64_t, uint64_t> wakes;
    EventBridge bridge{[this] { return now; }, [this](auto d, auto g) {
        wakes.emplace(checked_add(now, d), g);
    }, 100000, EventBridge::BackendMode::PlatformOnly};
    Runtime runtime{bridge, [this] { return now; }};
    HostCpu cpu{bridge, [this] { return now; }, 1};
    Fixture() { bridge.start(); }
    void until(uint64_t t) {
        while (!wakes.empty() && wakes.begin()->first <= t) {
            auto w = *wakes.begin(); wakes.erase(wakes.begin());
            now = w.first; bridge.wake(w.second);
        }
        now = t;
    }
    void drain() {
        while (!wakes.empty()) until(wakes.begin()->first);
        runtime.require_progress();
    }
    Adapter delay(uint64_t ns) {
        return [this, ns](Done done) {
            bridge.enqueue(checked_add(now, ns), EventBridge::Backend, "test actual arrival",
                           [done] { done({}); });
        };
    }
    Operation op(std::string key, uint64_t duration, std::vector<CompletionToken> in = {}) {
        Operation o;
        o.key = {0, std::move(key), "service"}; // Original query ID zero is valid.
        o.work = {{"items", {5,"count"}}};
        o.outputs = {{"out", "opaque", UINT64_MAX, 0, ""}}; // No payload allocation.
        o.duration_ns = duration; o.resource = &cpu; o.inputs = std::move(in);
        return o;
    }
};
void gates(uint64_t input, uint64_t service, uint64_t ret) {
    Fixture f;
    auto o = f.op("source", service);
    o.input = f.delay(input); o.send_return = f.delay(ret);
    auto a = f.runtime.submit(o);
    auto b = f.runtime.submit(f.op("child", 2, {a}));
    bool completed = false;
    uint64_t a_time = UINT64_MAX, b_time = UINT64_MAX;
    f.runtime.when_complete(a, [&](Outcome x) {
        check(!x.failure, "unexpected failure");
        a_time = f.now;
        check(f.runtime.describe(a).at(0).logical_bytes == UINT64_MAX, "metadata changed");
    });
    f.runtime.when_complete(b, [&](Outcome x) {
        check(!x.failure, "child failed"); b_time = f.now; completed = true;
    });
    rejects([&] { f.runtime.describe(a); });
    check(!completed && f.cpu.records().empty(), "synchronous submission");
    auto end = input + service + ret;
    if (end) {
        f.until(end-1);
        check(a_time == UINT64_MAX && b_time == UINT64_MAX, "early publication");
    }
    f.drain();
    check(a_time == end && b_time == end+2, "independent hand timing");
    check(f.cpu.records().size() == 2 && f.cpu.records()[1].start == end,
          "child ran before return");
    auto alias = a;
    auto owner = f.runtime.retain(a);
    f.runtime.release(a);
    rejects([&] { f.runtime.release(alias); });
    check(f.runtime.describe(owner).size() == 1, "retain lost result");
    f.runtime.release(owner); f.runtime.release(b); f.drain();
    check(!f.runtime.task_records() && !f.runtime.owners() && !f.runtime.pins(), "token leak");
}
void fifo(uint64_t slots) {
    Fixture f;
    HostCpu cpu(f.bridge, [&] { return f.now; }, slots);
    std::vector<CompletionToken> tokens;
    std::vector<uint64_t> order;
    for (uint64_t i=0; i<8; ++i) {
        auto o = f.op(std::to_string(i), 3); o.resource = &cpu;
        auto t = f.runtime.submit(o); tokens.push_back(t);
        f.runtime.when_complete(t, [&, i](Outcome x) { check(!x.failure, "FIFO failure"); order.push_back(i); });
    }
    f.drain();
    for (uint64_t i=0; i<8; ++i) {
        check(order[i] == i, "same-time FIFO reordered");
        check(cpu.records()[i].start == (i/slots)*3 && cpu.records()[i].end == (i/slots+1)*3,
              "FIFO slots hand timing");
        f.runtime.release(tokens[i]);
    }
    check(cpu.peak_busy() == slots && !cpu.pending() && !f.runtime.task_records(), "slots/reclaim");
}
void failures(const std::string& where) {
    Fixture f;
    auto blocker = f.runtime.submit(f.op("blocker", 20));
    auto o = f.op("fault", 10);
    if (where == "input") o.input = f.delay(10);
    if (where == "return") o.send_return = f.delay(10);
    if (where == "dependencies") o.inputs = {blocker};
    auto a = f.runtime.submit(o);
    auto child = f.runtime.submit(f.op("never", 1, {a}));
    bool failed = false;
    uint64_t failure_time = UINT64_MAX;
    f.runtime.when_complete(child, [&](Outcome x) {
        check(x.failure && x.failure->source == a.id(), "lost causal failure");
        failed = true; failure_time = f.now;
    });
    uint64_t when = where == "return" ? 31 : where == "running" ? 21 : 1;
    f.until(when);
    f.runtime.fail(a, "Injected", "test failure");
    check(!failed && f.runtime.state(a) == State::FailedDraining, "failure published inline");
    if (where == "input" || where == "running" || where == "return") {
        f.until(when);
        check(!failed, "failure published before issued service drained");
    }
    auto hold = f.runtime.retain(a);
    f.runtime.release(a); // Internal work and dependent pins must keep records alive.
    f.drain();
    uint64_t expected = where == "input" ? 10 : where == "running" ? 30 :
                        where == "return" ? 40 : 1;
    check(failed && failure_time == expected, "failure drain timing");
    for (const auto& s : f.cpu.records()) check(s.key != "never/service", "failed successor ran");
    if (where == "input" || where == "dependencies" || where == "queued")
        check(f.cpu.records().size() == 1, "new service after failure");
    rejects([&] { f.runtime.describe(hold); });
    f.runtime.release(hold); f.runtime.release(child); f.runtime.release(blocker); f.drain();
    check(!f.runtime.task_records() && !f.runtime.pins() && !f.cpu.pending(), "failed lifetime leak");
}
void buffer() {
    Fixture f;
    BufferModel b(f.bridge, [&] { return f.now; }, 10);
    std::vector<int> grants;
    uint64_t first = 0, second = 0;
    b.reserve(8, "query-until-close", [&](uint64_t id) {
        first = id; grants.push_back(1); b.pin(id);
    });
    auto cancel = b.reserve(10, "cancel-queued", [](auto) { throw std::logic_error("cancelled grant"); });
    b.reserve(5, "input-until-last-use", [&](auto id) { second = id; grants.push_back(2); });
    b.reserve(2, "result-until-return", [&](auto id) { grants.push_back(3); b.release(id); });
    rejects([&] { b.reserve(11, "oversized", [](auto) {}); });
    check(b.used() == 0 && grants.empty(), "synchronous credit reservation");
    f.drain();
    check(b.used() == 8 && b.waiting() == 3 && grants == std::vector<int>{1}, "credit FIFO bypass");
    check(b.cancel(cancel), "queued credit cancel");
    b.release(first);
    rejects([&] { b.release(first); });
    rejects([&] { b.pin(first); });
    f.drain();
    check(b.used() == 8 && b.waiting() == 2, "owner release ignored pin");
    b.unpin(first);
    check(b.used() == 8, "credit release skipped phase1");
    f.drain();
    check(grants == std::vector<int>({1,2,3}) && b.used() == 5, "credit wake FIFO");
    b.release(second); f.drain();
    check(!b.used() && !b.live() && !b.waiting() && b.peak() == 8, "credit leak");
    rejects([&] { b.unpin(first); });
    bool zero = false;
    b.reserve(0, "empty", [&](auto id) { zero = true; b.release(id); });
    check(!zero, "inline zero credit"); f.drain(); check(zero && !b.live(), "zero credit leak");
}
void negatives() {
    Fixture f, other;
    rejects([&] { FifoResource bad(f.bridge, [&] { return f.now; }, 0); });
    rejects([&] { BufferModel bad(f.bridge, [&] { return f.now; }, 0); });
    rejects([&] { f.runtime.submit(Operation{}); });
    auto a = f.runtime.submit(f.op("a", 1));
    auto o = other.op("foreign", 0, {a});
    rejects([&] { other.runtime.submit(o); });
    o = f.op("cross", 0, {a}); o.key.query = 1;
    rejects([&] { f.runtime.submit(o); });
    o = f.op("bad-unit", 1); o.work["items"].second = "";
    rejects([&] { f.runtime.submit(o); });
    o = f.op("duplicate-port", 1); o.outputs.push_back(o.outputs.front());
    rejects([&] { f.runtime.submit(o); });
    f.drain(); rejects([&] { f.runtime.fail(a, "bad", "terminal"); });
    f.runtime.release(a);
    f.now = UINT64_MAX;
    rejects([&] { f.runtime.submit(f.op("overflow", 1)); });
    check(!f.runtime.task_records(), "negative case leaked task");
    // Queue delay may overflow even when submission-time end fitted.
    Fixture g;
    g.now = UINT64_MAX-2;
    g.cpu.submit("first", 2, [] {}, [](bool) {});
    g.cpu.submit("second", 2, [] {}, [](bool) {});
    rejects([&] { g.drain(); });
}
void adapter_faults() {
    Fixture f;
    auto o = f.op("throws", 1);
    o.input = [](Done) { throw std::runtime_error("not issued"); };
    auto t = f.runtime.submit(o);
    bool failed = false;
    f.runtime.when_complete(t, [&](Outcome x) { failed = bool(x.failure); });
    f.drain(); check(failed && f.cpu.records().empty(), "adapter exception not fail closed");
    f.runtime.release(t); check(!f.runtime.task_records(), "adapter exception leak");
    Fixture g;
    Done completion;
    o = g.op("stalled", 0); o.input = [&](Done d) { completion = d; };
    t = g.runtime.submit(o);
    rejects([&] { g.drain(); }); // Empty queue cannot silently succeed with outstanding input.
    completion({}); g.drain();
    rejects([&] { completion({}); });
    g.runtime.release(t);
}
void fan_in(bool fail) {
    Fixture f;
    HostCpu independent(f.bridge, [&] { return f.now; }, 2);
    auto op = f.op("slow", 20); op.resource = &independent;
    auto slow = f.runtime.submit(op);
    op = f.op("fast", 5); op.resource = &independent;
    auto fast = f.runtime.submit(op);
    auto join = f.runtime.submit(f.op("join", 0, {slow, fast}));
    uint64_t end = UINT64_MAX;
    f.runtime.when_complete(join, [&](Outcome x) {
        check(bool(x.failure) == fail, "join outcome"); end = f.now;
    });
    if (fail) {
        f.until(1); f.runtime.fail(fast, "Injected", "second input failed");
    }
    f.runtime.release(slow); f.runtime.release(fast); f.runtime.release(join);
    f.drain();
    check(end == (fail ? 5 : 20), "join failed-input precedence/visibility");
    check(f.cpu.records().size() == (fail ? 0 : 1), "failed join got service");
    check(!f.runtime.task_records() && !f.runtime.pins(), "fan-in leak");
}
void early_release() {
    Fixture f;
    size_t observed = 0;
    f.runtime.observations().sink = [&](const Transition&) { ++observed; };
    f.cpu.observations().enabled = false;
    auto a = f.runtime.submit(f.op("owner-released", 3));
    auto b = f.runtime.submit(f.op("pinned-consumer", 4, {a}));
    bool done = false;
    f.runtime.when_complete(b, [&](Outcome x) { check(!x.failure && f.now == 7, "early release timing"); done = true; });
    f.runtime.release(a); f.runtime.release(b);
    f.drain();
    check(done && !f.runtime.task_records() && !f.runtime.owners() && !f.runtime.pins(), "early release leak");
    check(observed > 0 && f.runtime.transitions().empty() && f.cpu.records().empty() &&
          f.cpu.observations().count == 2, "streaming/statistics retention");
}
void failed_buffer_drain() {
    Fixture f;
    BufferModel buffer(f.bridge, [&] { return f.now; }, 8);
    auto o = f.op("inflight-buffer", 1);
    o.input = [&](Done done) {
        buffer.reserve(8, "source-until-input-arrival", [&, done](uint64_t credit) {
            buffer.pin(credit);
            buffer.release(credit); // Source owner is gone; actual transfer still pins it.
            f.bridge.enqueue(10, EventBridge::Backend, "inflight buffer arrival", [&, done, credit] {
                buffer.unpin(credit);
                done({});
            });
        });
    };
    auto t = f.runtime.submit(o);
    bool failed = false;
    f.runtime.when_complete(t, [&](Outcome x) {
        check(x.failure && f.now == 10 && !buffer.used() && !buffer.live(),
              "failure published before pinned transfer credit released");
        failed = true;
    });
    f.until(1);
    check(buffer.used() == 8, "inflight source pin missing");
    f.runtime.fail(t, "Injected", "input in flight");
    f.until(9); check(!failed && buffer.used() == 8, "failure fabricated buffer drain");
    f.drain(); f.runtime.release(t);
    check(failed && !f.runtime.task_records() && f.cpu.records().empty(), "failed buffer cleanup");
}
void phases() {
    Fixture f;
    std::vector<int> order;
    f.bridge.enqueue(0, EventBridge::ContinueSubmit, "continue", [&] {
        order.push_back(3);
        f.bridge.enqueue(0, EventBridge::Publish, "backward phase", [&] { order.push_back(5); });
    });
    f.bridge.enqueue(0, EventBridge::ContinueSubmit, "same phase FIFO", [&] { order.push_back(4); });
    f.bridge.enqueue(0, EventBridge::Publish, "publish", [&] { order.push_back(2); });
    f.bridge.enqueue(0, EventBridge::ResourceCompleteRelease, "release", [&] { order.push_back(1); });
    f.bridge.enqueue(0, EventBridge::Backend, "backend", [&] { order.push_back(0); });
    f.drain();
    check(order == std::vector<int>({0,1,2,3,4,5}) && f.now == 0, "four phases/delta FIFO");
}
int main() {
    try {
        for (const auto& ns : std::vector<std::vector<uint64_t>>{{0,0,0},{3,5,7},{103,5,7},{3,105,7},{3,5,107}})
            gates(ns[0], ns[1], ns[2]);
        for (auto slots : {1,2,4}) fifo(slots);
        for (const auto& where : {"dependencies","queued","running","input","return"}) failures(where);
        buffer(); negatives(); adapter_faults(); failed_buffer_drain(); fan_in(false); fan_in(true); early_release(); phases();
        std::puts("P07 PASS hand timing / delay injection / zero / FIFO 1,2,4 / failure drain / pins / negatives / phases");
    } catch (const std::exception& e) { std::fprintf(stderr, "FAIL %s\n", e.what()); return 1; }
}
