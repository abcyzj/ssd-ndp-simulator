#pragma once
#include "ir/executor.h"
#include <iostream>
#include <stdexcept>

namespace ndpsim::ir::test {
inline void check(bool condition, const char* detail) {
    if (!condition)
        throw std::logic_error(detail);
}
inline Document contract(const Document& example) {
    auto d = example;
    d.operations.clear();
    return d;
}
inline Adapter adapter(runtime::Runtime& rt,
                       EventBridge& bridge,
                       std::function<uint64_t()> now,
                       Values values,
                       uint64_t service = 0,
                       uint64_t ret = 0,
                       std::function<void()> grant = {}) {
    Adapter a;
    a.grant = [&rt, grant](auto id) {
        if (grant)
            grant();
        rt.start(id);
    };
    a.evaluate = [values = std::move(values)](const Values&) {
        return values;
    };
    a.service = [&rt, &bridge, now, service](auto id) {
        bridge.enqueue(
            now() + service, EventBridge::ResourceCompleteRelease, "IR probe service", [&rt, id] {
                rt.service_done(id);
            });
    };
    a.needs_return = ret != 0;
    a.send_return = [&rt, &bridge, now, ret](auto id) {
        bridge.enqueue(
            now() + ret, EventBridge::ResourceCompleteRelease, "IR probe return", [&rt, id] {
                rt.return_done(id);
            });
    };
    return a;
}
inline Values ready(std::shared_ptr<const StateSnapshot> state) {
    return {{"query", {Json::array({1., 2., 3., 4.}), {}}},
            {"state", {nullptr, std::move(state)}},
            {"ready", {nullptr, {}}}};
}
// Shared by native probes and a real SST SelfLink component. Costs are synthetic
// boundary probes; these are not the V03-3 graph/operator placement mappings.
class Scenario {
public:
    Executor executor;
    std::vector<Lease> leases;
    unsigned completed = 0, probes = 0;
    uint64_t successor_time = 0, independent_time = UINT64_MAX;
    Scenario(const Document& d,
             runtime::Runtime& rt,
             EventBridge& bridge,
             std::function<uint64_t()> now,
             uint64_t input_delay = 0,
             uint64_t compute_delay = 0,
             uint64_t return_delay = 0)
        : executor(contract(d), rt, now) {
        auto state = StateSnapshot::initial(7, "search-state", {{"expanded", Json::array()}});
        auto newer = StateSnapshot::advance(state, {{"expanded", Json::array({1u})}});
        check(state->identity().version == 1 && newer->identity().version == 2 &&
                  state->data()["expanded"].empty(),
              "state snapshot mutation");
        auto operations = d.operations;
        operations[3].deps.push_back({1, DependencyKind::Order, "state", 1});
        operations[5].deps.push_back({1, DependencyKind::Order, "state", 1});
        auto q = executor.submit(
            operations[0], {}, adapter(rt, bridge, now, ready(state), 10 + input_delay));
        auto score_adapter = adapter(rt, bridge, now, {}, 20 + compute_delay, 30 + return_delay);
        score_adapter.evaluate = [](const Values& v) {
            check(v.at("query").data()[0] == 1. && v.at("ids").data()[0] == 0u, "real input binding");
            return Values{{"scores", {Json::array({{{"id", 0u}, {"distance", 42.}}}), {}}}};
        };
        auto score = executor.submit(operations[1], {q}, std::move(score_adapter));
        auto merge_adapter = adapter(rt, bridge, now, {});
        merge_adapter.evaluate = [](const Values& v) {
            return Values{{"scores", v.at("batch")}};
        };
        auto merge = executor.submit(operations[2], {score}, std::move(merge_adapter));
        auto select_adapter = adapter(rt, bridge, now, {});
        select_adapter.evaluate = [state](const Values& v) {
            check(v.at("expanded").state == state && v.at("scores").data()[0]["distance"] == 42.,
                  "state/data binding");
            return Values{{"ids", {Json::array({1u}), {}}}};
        };
        auto select = executor.submit(operations[3], {q, merge}, std::move(select_adapter));
        auto read_adapter = adapter(rt, bridge, now, {}, 5);
        read_adapter.evaluate = [](const Values& v) {
            check(v.at("ids").data() == Json::array({1u}), "dynamic address binding");
            return Values{
                {"adjacency", {Json::array({{{"degree", 1u}, {"ids", Json::array({2u})}}}), {}}}};
        };
        auto read = executor.submit(operations[4], {select}, std::move(read_adapter));
        auto dedup_adapter = adapter(rt, bridge, now, {});
        dedup_adapter.evaluate = [](const Values& v) {
            return Values{{"ids", {v.at("adjacency").data()[0]["ids"], {}}}};
        };
        auto dedup = executor.submit(operations[5], {q, read}, std::move(dedup_adapter));
        auto joined = executor.join(operations[6], {score, dedup});
        leases = {q, score, merge, select, read, dedup, joined};
        executor.when_all({joined}, 100, [this, now, dedup](runtime::Outcome o) {
            check(!o.failure && executor.get(dedup, "ids").data() == Json::array({2u}),
                  "join result");
            successor_time = now();
            ++completed;
            std::cout << "IR_CAUSAL successor=" << now() << "\n";
        });
        auto independent = operations[0];
        independent.op_id = 8;
        independent.scope.query = 8;
        independent.scope.query_ordinal = 1;
        auto other = executor.submit(
            independent,
            {},
            adapter(rt, bridge, now, ready(StateSnapshot::initial(8, "search-state", nullptr)), 7));
        leases.push_back(other);
        executor.when_all({other}, 101, [this, now](runtime::Outcome o) {
            check(!o.failure && now() == 7, "independent query blocked");
            independent_time = now();
            ++completed;
            std::cout << "IR_CAUSAL independent=" << now() << "\n";
        });
        for (auto t : {1 + input_delay,
                       29 + input_delay + compute_delay,
                       59 + input_delay + compute_delay + return_delay})
            bridge.enqueue(t, EventBridge::ContinueSubmit, "IR early visibility", [this, score] {
                try {
                    executor.get(score, "scores");
                    throw std::logic_error("IR premature output");
                } catch (const content::Error& e) {
                    check(e.code == content::Code::NotReady, "wrong early error");
                }
                ++probes;
            });
    }
    void finish(uint64_t expected) {
        check(completed == 2 && probes == 3 && successor_time == expected && independent_time == 7,
              "causal assertions");
        for (auto lease : leases)
            executor.release(lease);
        leases.clear();
        const auto c = executor.counts();
        check(c.submitted == 8 && c.completed == 8 && c.failed == 0 && c.active_ops == 0 &&
                  c.active_values == 0,
              "IR records leaked");
        std::cout << "IR_COUNTS submitted=" << c.submitted << " completed=" << c.completed
                  << " failed=" << c.failed << " peak_ops=" << c.peak_ops
                  << " peak_values=" << c.peak_values << " active_ops=0 active_values=0\n";
    }
};
} // namespace ndpsim::ir::test
