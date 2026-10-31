#include "runtime/runtime.h"
#include "backend/observation.h"
#include "observation/ids.h"
#undef NDEBUG
#include <cassert>
#include <random>
#include <iostream>
using namespace ndpsim;
int main() {
    // Arbitrarily ordered, overlapping, clipped and zero-length intervals, enough
    // to force multiple external merge runs. Independent point coverage oracle.
    {
        Observation o;
        std::mt19937 gen(42);
        std::vector<bool> covered(10000);
        for (int i = 0; i < 20000; ++i) {
            uint64_t a = gen() % 10000, b = std::min(uint64_t(10000), a + gen() % 20);
            MQSimObservation::sink("command_bus", a, b, 0, 0, 0);
            for (auto t = std::max(uint64_t(71), a); t < std::min(uint64_t(8911), b); ++t)
                covered[t] = true;
        }
        uint64_t expected = std::count(covered.begin(), covered.end(), true);
        unsigned stats = 0;
        o.emit = [&](bool stat, const std::string& row) {
            if (stat) {
                ++stats;
                assert(row.find("\"union_ns\":" + std::to_string(expected) + ",") != row.npos);
            }
        };
        o.report(71, 8911);
        assert(stats == 2);
        o.reset_window();
        stats = 0;
        o.report(0, 1);
        assert(!stats);
    }
    Simulator->Reset();
    Simulator->Initialize_simulation();
    uint64_t now = 0;
    std::multimap<uint64_t, uint64_t> wakes;
    EventBridge b(
        [&] {
            return now;
        },
        [&](auto d, auto g) {
            wakes.emplace(now + d, g);
        });
    runtime::Runtime rt(b, [&] {
        return now;
    });
    rt.statistics_only();
    b.start();
    content::ContentStore store(
        std::vector<content::Input>{{{1, content::Kind::Query, 0, 1}, {1}}});
    content::Handles handles(store);
    handles.statistics_only();
    auto drain = [&] {
        while (!wakes.empty()) {
            auto w = *wakes.begin();
            wakes.erase(wakes.begin());
            now = w.first;
            b.wake(w.second);
        }
    };
    runtime::Handle stale;
    for (int i = 0; i < 10000; ++i) {
        runtime::Operation op;
        op.query = i + 1;
        op.grant = [&](auto id) {
            rt.start(id);
        };
        op.evaluate = [](const auto&) {
            return content::Bytes{42};
        };
        op.service = [&](auto id) {
            b.enqueue(now + 1, EventBridge::ResourceCompleteRelease, "test", [&, id] {
                rt.service_done(id);
            });
        };
        auto h = rt.submit(std::move(op));
        stale = h;
        rt.when_all({h, h}, h.id(), [&, h](auto outcome) {
            assert(!outcome.failure);
            assert(rt.try_get(h)[0] == 42);
            rt.release(h);
        });
        drain();
        assert(rt.task_records() == 0 && rt.wait_records() == 0);
        auto c = handles.create({{{1, content::Kind::Query, 0, 1}, {0, 1}}},
                                content::Location::HostMemory,
                                {1, 1, now},
                                true);
        auto copy = c;
        auto pin = handles.pin(c);
        handles.release(c);
        try {
            handles.release(copy);
            assert(false);
        } catch (const content::Error& e) {
            assert(e.code == content::Code::Released);
        }
        handles.release(pin);
        assert(handles.lease_records() == 0 && handles.live_records() == 0);
    }
    assert(rt.peak_tasks() == 1 && rt.transitions().empty() && handles.releases().empty());
    try {
        rt.try_get(stale);
        assert(false);
    } catch (const content::Error& e) {
        assert(e.code == content::Code::Released);
    }
    try {
        rt.service_done(stale.id());
        assert(false);
    } catch (const content::Error& e) {
        assert(e.code == content::Code::InvalidHandle);
    }
    for (bool returning : {false, true}) {
        runtime::Operation op;
        op.query = 20000;
        op.grant = [&](auto id) {
            rt.start(id);
        };
        op.evaluate = [](const auto&) {
            return content::Bytes{1};
        };
        op.needs_return = returning;
        op.service = [&](auto id) {
            if (returning)
                rt.service_done(id);
        };
        op.send_return = [](auto, size_t) {};
        auto h = rt.submit(std::move(op));
        drain();
        rt.fail(h.id(), content::Code::BackendFailure, "late failure");
        rt.release(h);
        assert(rt.task_records() == 1); // adapter completion still owns the association
        try {
            if (returning)
                rt.return_done(h.id());
            else
                rt.service_done(h.id());
            assert(false);
        } catch (const content::Error& e) {
            assert(e.code == content::Code::BackendFailure);
        }
        assert(rt.task_records() == 0);
    }
    CompletedIds ids;
    for (uint64_t i = 1; i <= 10000; i += 2) {
        assert(ids.insert(i + 1));
        assert(ids.insert(i));
    }
    assert(ids.size() == 1 && ids.contains(1) && ids.contains(10000) && !ids.contains(10001) &&
           !ids.insert(2));
    std::cout << "T5 lifecycle/lease/non-reuse/external-sort PASS\n";
}
