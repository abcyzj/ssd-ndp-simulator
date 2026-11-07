#include "controller_timing_scenario.h"
#include "backend/ordinary_device.h"
#include <cstdio>
using namespace ndpsim;
using namespace ndpsim::timing;
using ndpsim::test::p12::Scenario;
static void check(bool x, const char* s) {
    if (!x)
        throw std::logic_error(s);
}
template <class F> void rejects(F f) {
    bool caught = false;
    try {
        f();
    } catch (const std::exception&) {
        caught = true;
    }
    check(caught, "negative accepted");
}
struct Driver {
    uint64_t now = 0;
    std::multimap<uint64_t, uint64_t> wakes;
    EventBridge bridge{[this] {
                           return now;
                       },
                       [this](auto d, auto g) {
                           wakes.emplace(checked_add(now, d), g);
                       }};
    void drain(std::function<void()> poll = {}) {
        unsigned n = 0;
        while (!wakes.empty()) {
            check(++n < 100000, "driver stalled");
            auto w = *wakes.begin();
            wakes.erase(wakes.begin());
            now = w.first;
            bridge.wake(w.second);
            if (poll)
                poll();
        }
    }
};
static Message message(MessageKind k, uint64_t task = 1) {
    Message m;
    m.kind = k;
    m.query = 0;
    m.task = task;
    m.version = 1;
    if (k == MessageKind::Open) {
        m.count = m.dimension = 32;
    } else
        m.token = 1;
    if (k == MessageKind::Execute || k == MessageKind::Result) {
        m.count = 3;
        m.chunk = 1;
    }
    if (k == MessageKind::Execute)
        m.dimension = 32;
    if (k == MessageKind::Error)
        m.status = 5;
    return m;
}
static void messages() {
    MessageRules rules;
    for (auto [kind, payload, logical] :
         std::vector<std::tuple<MessageKind, uint64_t, uint64_t>>{{MessageKind::Open, 192, 128},
                                                                  {MessageKind::OpenAck, 48, 0},
                                                                  {MessageKind::Execute, 76, 12},
                                                                  {MessageKind::Result, 92, 36},
                                                                  {MessageKind::Close, 48, 0},
                                                                  {MessageKind::CloseAck, 48, 0},
                                                                  {MessageKind::Error, 48, 0}}) {
        auto s = rules.size(message(kind));
        check(s.payload == payload && s.logical == logical, "independent envelope formula");
    }
    auto m = message(MessageKind::Open);
    for (unsigned i = 0; i < 8; ++i) {
        auto bad = m;
        if (i == 0)
            bad.protocol = "unknown";
        if (i == 1)
            bad.task = 0;
        if (i == 2)
            bad.version = 0;
        if (i == 3)
            bad.token = 1;
        if (i == 4)
            bad.status = 1;
        if (i == 5)
            bad.count++;
        if (i == 6)
            bad.count = bad.dimension = UINT64_MAX;
        if (i == 7)
            bad.kind = static_cast<MessageKind>(99);
        rejects([&] {
            rules.size(bad);
        });
    }
    rejects([&] {
        rules.add("graph-readonly/1", [](auto) {
            return MessageSize{0, 0};
        });
    });
    rules.add("opaque/1", [](auto) {
        return MessageSize{123456789, 123456800};
    });
    auto big = m;
    big.protocol = "opaque/1";
    check(rules.size(big).logical == 123456789, "extension size rule");
    Conversation c;
    c.arrive(m);
    c.arrive(message(MessageKind::OpenAck));
    auto cmd = message(MessageKind::Execute, 2);
    c.arrive(cmd);
    rejects([&] {
        c.arrive(message(MessageKind::Close, 3));
    });
    auto wrong = message(MessageKind::Result, 2);
    wrong.count++;
    rejects([&] {
        c.arrive(wrong);
    });
    wrong = message(MessageKind::Result, 2);
    wrong.version++;
    rejects([&] {
        c.arrive(wrong);
    });
    c.arrive(message(MessageKind::Result, 2));
    rejects([&] {
        c.arrive(message(MessageKind::Result, 2));
    });
    c.arrive(message(MessageKind::Close, 3));
    c.arrive(message(MessageKind::CloseAck, 3));
    check(!c.sessions() && !c.pending() && !c.task_records(), "conversation leak");
    rejects([&] {
        c.arrive(m);
    });
    std::puts("P12 message rules/negative/conversation PASS");
}
static void partial_credit() {
    Simulator->Reset();
    Simulator->Initialize_simulation();
    Driver f;
    BufferModel buffer(
        f.bridge,
        [&] {
            return f.now;
        },
        100);
    uint64_t a = 0;
    unsigned grants = 0;
    buffer.reserve(80, "atomic block", [&](auto id) {
        a = id;
        ++grants;
    });
    buffer.reserve(50, "waiting block", [&](auto id) {
        ++grants;
        buffer.release(id);
    });
    f.bridge.start();
    f.drain();
    check(grants == 1 && buffer.used() == 80, "credit FIFO");
    buffer.pin(a);
    rejects([&] {
        buffer.release_part(a, 10);
    });
    buffer.unpin(a);
    rejects([&] {
        buffer.release_part(a, 81);
    });
    buffer.release_part(a, 40);
    check(buffer.used() == 80, "partial release before phase1");
    f.drain();
    check(grants == 2 && buffer.used() == 40, "partial credit failed to wake waiter");
    buffer.release(a);
    f.drain();
    check(!buffer.used() && !buffer.live(), "partial credit leak");
    Simulator->Reset();
}
static void isolated(double rate) {
    using namespace Host_Components;
    Simulator->Reset();
    PCIe_Link link("size-only", nullptr, nullptr, rate);
    Simulator->AddObject(&link);
    Simulator->Initialize_simulation();
    Driver f;
    MessageRules rules;
    MetadataPcie pcie(
        link,
        f.bridge,
        [&] {
            return f.now;
        },
        rules);
    unsigned done = 0;
    std::vector<PCIe_Link::Transfer> starts;
    link.observe = [&](const char* s, const auto& t) {
        if (std::string(s) == "start")
            starts.push_back(t);
    };
    pcie.send(message(MessageKind::Open), [&](auto) {
        ++done;
    });
    pcie.send(message(MessageKind::Open), [&](auto) {
        ++done;
    });
    pcie.send(message(MessageKind::OpenAck), [&](auto) {
        ++done;
    });
    check(!done && pcie.pending() == 3, "inline PCIe completion");
    f.bridge.start();
    f.drain();
    check(done == 3 && !pcie.pending() && starts.size() == 3, "PCIe drain");
    // 192+2*28=248B; per-lane ceil=62; ACK48+28=76, per-lane=19.
    auto h = uint64_t(62 / rate), d = uint64_t(19 / rate);
    unsigned hcount = 0;
    for (auto& r : pcie.records()) {
        if (r.direction == Direction::H2D) {
            ++hcount;
            check(r.wire == 248 && r.arrived == h * hcount, "wire/FIFO H2D");
        } else
            check(r.wire == 76 && r.arrived == d, "wire/duplex D2H");
    }
    // Large virtual payload uses a constant-sized envelope and no backing allocation.
    rules.add("large/1", [](auto) {
        return MessageSize{100000000, 100000064};
    });
    auto big = message(MessageKind::Open);
    big.protocol = "large/1";
    pcie.send(big, [&](auto) {
        ++done;
    });
    f.drain();
    check(done == 4, "large size-only transfer");
    rules.add("empty/1", [](auto) {
        return MessageSize{0, 0};
    });
    auto empty = message(MessageKind::Open);
    empty.protocol = "empty/1";
    auto before = f.now;
    pcie.send(empty, [&](auto) {
        ++done;
    });
    check(done == 4, "zero payload inline");
    f.drain();
    check(done == 5 && f.now == before, "zero payload added time");
    Simulator->Reset();
}
// Migrate the local-read completion guards from the payload adapter.
static void local_reads(const std::string& mode) {
    Driver driver;
    OrdinaryDevice device(512, ndpsim::test::device_fixture());
    auto& host = *static_cast<SSD_Components::Host_Interface_NVMe*>(device.device->Host_interface);
    MetadataLocalReads reads(host, driver.bridge, [&] { return driver.now; });
    layout::Plan plan{1080, 2048, {{7, 3, {}}, {32, 1, {}}}};
    bool launched = false;
    unsigned local = 0, ordinary = 0, dma = 0;
    std::vector<MetadataLocalReads::Request> held;
    std::string rejection;
    device.root->data_arrived = [&](uint64_t address, unsigned bytes) {
        if (launched)
            dma += bytes;
        device.flow->data(address, bytes);
    };
    device.flow->completed = [&](ExternalFlow::Completion c) {
        if (launched) {
            check(!c.write && c.lba == 48 && c.sectors == 1, "ordinary extent after local reads");
            ++ordinary;
        }
    };
    device.start();
    driver.bridge.start();
    driver.drain([&] {
        if (!launched && device.window->advance(!Simulator->Peek_next_time(), driver.now)) {
            launched = true;
            auto bad = plan;
            bad.reads.back().lba = UINT64_MAX;
            auto initial = host.Pending_local_reads();
            rejects([&] { reads.submit(bad, {0, "bad", "test"}, [](auto) {}); });
            check(!reads.pending() && host.Pending_local_reads() == initial &&
                      !Simulator->Peek_next_time(), "partial invalid local submission");
            for (unsigned i = 0; i < 2; ++i)
                reads.submit(plan, {i, "read", "test"}, [&, i](auto c) {
                    check(c.context.query == i && c.context.op == "read" &&
                              c.requests.size() == 2 && c.plan.logical_bytes == 1080 &&
                              c.plan.sector_bytes == 2048 && !dma, "local aggregate/identity/DMA");
                    if (++local == 2) {
                        check(!reads.pending() && !reads.pending_requests() &&
                                  !host.Pending_local_reads(), "local records retained");
                        device.window->read(48, 1);
                    }
                });
        }
        if (!rejection.empty())
            return;
        for (auto& c : host.Take_local_completions())
            held.push_back(std::move(c));
        if (held.size() != 4)
            return;
        std::reverse(held.begin(), held.end());
        try {
            if (mode == "request")
                ++held.front().bytes;
            if (mode == "transaction")
                held.front().transactions.front().completed = false;
            if (mode == "unknown")
                held.front().token = UINT64_MAX;
            if (mode == "duplicate")
                reads.accept(held.front());
            for (auto& c : held)
                reads.accept(std::move(c));
        } catch (const std::runtime_error& e) {
            rejection = e.what();
        }
        held.clear();
        check(!local, "local completion published inline");
    });
    if (mode == "reverse") {
        check(rejection.empty() && local == 2 && ordinary == 1 && dma == 512 &&
                  driver.bridge.idle() && !device.flow->pending(), "local/ordinary drain");
    } else {
        const std::string expected = mode == "request" ? "request metadata/time mismatch" :
                                     mode == "transaction" ? "transaction metadata/completion mismatch" :
                                     "unknown/duplicate local completion";
        check(rejection.find(expected) != std::string::npos && !local && !ordinary && !dma,
              "corrupt completion was accepted or reached a successor");
        rejects([&] { reads.submit(plan, {0, "retry", "test"}, [](auto) {}); });
    }
}
int main(int argc, char** argv) {
    try {
        if (argc > 2 && std::string(argv[1]) == "local") {
            local_reads(argv[2]);
        } else if (argc > 1) {
            Driver f;
            Scenario s(
                f.bridge,
                [&] {
                    return f.now;
                },
                argv[1]);
            s.start();
            f.bridge.start();
            f.drain([&] {
                s.poll();
            });
            s.verify();
        } else {
            messages();
            partial_credit();
            isolated(1);
            isolated(2);
            isolated(1000000);
            Driver f;
            Scenario s(f.bridge, [&] {
                return f.now;
            });
            s.start();
            f.bridge.start();
            f.drain([&] {
                s.poll();
            });
            s.verify();
        }
    } catch (const std::exception& e) {
        std::fprintf(stderr, "%s\n", e.what());
        return 1;
    }
}
