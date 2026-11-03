#include "ir_scenario.h"
using namespace ndpsim;
using namespace ndpsim::ir;
using namespace ndpsim::ir::test;
struct Fixture {
    uint64_t now = 0;
    std::multimap<uint64_t, uint64_t> wakes;
    EventBridge bridge{[this] {
                           return now;
                       },
                       [this](auto d, auto g) {
                           wakes.emplace(now + d, g);
                       }};
    runtime::Runtime rt{bridge, [this] {
                            return now;
                        }};
    Fixture() {
        Simulator->Reset();
        Simulator->Initialize_simulation();
        bridge.start();
        rt.statistics_only();
    }
    void drain() {
        while (!wakes.empty()) {
            auto w = *wakes.begin();
            wakes.erase(wakes.begin());
            now = w.first;
            bridge.wake(w.second);
            rt.require_progress();
        }
        rt.require_progress();
    }
    Adapter a(Values values = {}, uint64_t service = 0) {
        return adapter(
            rt,
            bridge,
            [this] {
                return now;
            },
            std::move(values),
            service);
    }
    void empty() {
        drain();
        check(!rt.task_records() && !rt.wait_records() && !rt.private_slots(),
              "Runtime records leaked");
    }
};
template <class F> void rejects(const std::string& code, F f) {
    try {
        f();
        throw std::logic_error("missing rejection " + code);
    } catch (const Error& e) {
        check(e.code == code || code.empty(), e.what());
    }
}
Operation empty_join(const Document& d, uint64_t id) {
    auto o = d.operations.back();
    o.op_id = id;
    o.inputs.clear();
    o.deps.clear();
    return o;
}
void guards(const Document& d) {
    Fixture f;
    Executor ex(contract(d), f.rt, [&] {
        return f.now;
    });
    auto state = StateSnapshot::initial(7, "search-state", nullptr);
    auto q = ex.submit(d.operations[0], {}, f.a(ready(state), 10));
    auto qcopy = q;
    auto retained = ex.retain(q);
    auto score = d.operations[1];
    for (auto mode : {0, 1, 2, 3, 4, 5}) {
        auto bad = score;
        if (mode == 0)
            bad.op_id = 1;
        if (mode == 1)
            bad.deps[0].producer_op = 2;
        if (mode == 2)
            bad.scope.query = 8;
        if (mode == 3)
            bad.deps.erase(bad.deps.begin());
        if (mode == 4)
            bad.inputs[0].value.type.shape[0].extent = 3;
        if (mode == 5)
            bad.scope.run = "foreign";
        rejects("", [&] {
            ex.submit(bad, {q}, f.a());
        });
    }
    ex.release(q);
    rejects("Released", [&] {
        ex.submit(score, {qcopy}, f.a());
    });
    rejects("Released", [&] {
        ex.release(qcopy);
    });
    // Retained consumer admission survives original lease release.
    auto s = ex.submit(
        score, {retained}, f.a({{"scores", {Json::array({{{"id", 0u}, {"distance", 1.}}}), {}}}}));
    ex.release(retained);
    f.drain();
    check(ex.get(s, "scores").data.size() == 1, "in-flight ownership");
    ex.release(s);
    f.empty();
    rejects("Released", [&] {
        ex.submit(empty_join(d, 3), {qcopy}, f.a());
    });
    check(ex.counts().active_ops == 0, "guard records");
    std::cout << "GUARDS PASS self/future/scope/missing-edge/type/released/retain\n";
}
void async_and_growth(const Document& d) {
    Fixture f;
    Executor ex(contract(d), f.rt, [&] {
        return f.now;
    });
    bool returned = false, resumed = false;
    ex.when_all({}, 1, [&](auto o) {
        check(returned && !o.failure, "empty wait inline");
        resumed = true;
    });
    auto h = ex.join(empty_join(d, 1), {});
    try {
        ex.get(h, "done");
        throw std::logic_error("zero join inline");
    } catch (const content::Error& e) {
        check(e.code == content::Code::NotReady, "zero join error");
    }
    returned = true;
    ex.release(h);
    f.empty();
    check(resumed, "empty wait lost");
    // Fixed concurrency/shape, growing completed count. No historic DAG/state table.
    for (uint64_t id = 2; id <= 2001; ++id) {
        auto lease = ex.join(empty_join(d, id), {});
        ex.when_all({lease, lease}, id, [](auto o) {
            check(!o.failure, "duplicate wait failure");
        });
        ex.release(lease);
        f.empty();
        check(ex.counts().active_ops == 0 && ex.counts().active_values == 0, "growth leak");
    }
    check(ex.counts().peak_ops == 1 && ex.counts().peak_values == 1, "history growth");
    std::cout << "GROWTH PASS submitted=2001 peak_ops=1 peak_values=1 terminal=0\n";
}
void dynamic_failure(const Document& d, bool invalid_state, bool malformed_output = false) {
    Fixture f;
    Executor ex(contract(d), f.rt, [&] {
        return f.now;
    });
    auto state = StateSnapshot::initial(7, "search-state", nullptr);
    auto q = ex.submit(d.operations[0], {}, f.a(ready(state)));
    auto score = ex.submit(d.operations[1],
                           {q},
                           f.a({{"scores", {Json::array({{{"id", 0u}, {"distance", 1.}}}), {}}}}));
    auto merge = ex.submit(d.operations[2], {score}, f.a({{"scores", {Json::array(), {}}}}));
    auto selectop = d.operations[3];
    selectop.deps.push_back({1, DependencyKind::Order, "state", 1});
    if (invalid_state)
        std::get<StateRef>(selectop.inputs[1].value.reference).state_id = "wrong";
    unsigned grants = 0;
    auto a = f.a({{"ids", {malformed_output ? Json::array({0u, 1u}) : Json::array({99u}), {}}}});
    a.grant = [&](auto id) {
        ++grants;
        f.rt.start(id);
    };
    auto select = ex.submit(selectop, {q, merge}, std::move(a));
    unsigned reads = 0;
    auto read_adapter = f.a();
    read_adapter.grant = [&](auto id) {
        ++reads;
        f.rt.start(id);
    };
    auto read = ex.submit(d.operations[4], {select}, std::move(read_adapter));
    bool failed = false;
    ex.when_all({read}, 1, [&](runtime::Outcome o) {
        check(o.failure.has_value(), "failure lost");
        failed = true;
        auto expected = invalid_state      ? content::Code::VersionMismatch
                        : malformed_output ? content::Code::InvalidArgument
                                           : content::Code::OutOfRange;
        check(o.failure->code == expected &&
                  o.failure->detail.find("time_ns=") != std::string::npos,
              "failure root/context lost");
    });
    for (auto lease : {q, score, merge, select, read})
        ex.release(lease);
    f.empty();
    check(failed && !reads && grants == (invalid_state ? 0u : 1u),
          "failed operation granted successor resource");
    check(ex.counts().active_ops == 0 &&
              ex.counts().failed == (invalid_state || malformed_output ? 2u : 1u),
          "failed records leaked");
    std::cout << "FAILURE PASS state=" << invalid_state << " shape=" << malformed_output
              << " successor_grants=0 terminal=0\n";
}

void dynamic_successor(const Document& d, uint32_t selected_id) {
    Fixture f;
    Executor ex(contract(d), f.rt, [&] {
        return f.now;
    });
    auto state = StateSnapshot::initial(7, "search-state", nullptr);
    auto q = ex.submit(d.operations[0], {}, f.a(ready(state)));
    auto score =
        ex.submit(d.operations[1],
                  {q},
                  f.a({{"scores", {Json::array({{{"id", selected_id}, {"distance", 1.}}}), {}}}}));
    auto merge_a = f.a();
    merge_a.evaluate = [](const Values& v) {
        return Values{{"scores", v.at("batch")}};
    };
    auto merge = ex.submit(d.operations[2], {score}, std::move(merge_a));
    auto select_op = d.operations[3];
    select_op.deps.push_back({1, DependencyKind::Order, "state", 1});
    auto select_a = f.a();
    select_a.evaluate = [](const Values& v) {
        return Values{{"ids", {Json::array({v.at("scores").data[0]["id"]}), {}}}};
    };
    auto select = ex.submit(select_op, {q, merge}, std::move(select_a));
    bool called = false;
    ex.when_all({select}, 9, [&](runtime::Outcome o) {
        check(!o.failure && ex.counts().submitted == 4, "successor prebuilt");
        auto visible = ex.get(select, "ids").data[0].get<uint32_t>();
        check(visible == selected_id, "real decision lost");
        auto a = f.a();
        a.evaluate = [&, visible](const Values& v) {
            check(v.at("ids").data[0] == visible, "successor read wrong ID");
            called = true;
            return Values{
                {"adjacency", {Json::array({{{"degree", 0u}, {"ids", Json::array()}}}), {}}}};
        };
        auto next = ex.submit(d.operations[4], {select}, std::move(a));
        ex.release(next);
        ex.release(select);
    });
    for (auto lease : {q, score, merge})
        ex.release(lease);
    f.empty();
    check(called && ex.counts().submitted == 5 && ex.counts().active_ops == 0,
          "dynamic successor not drained");
    std::cout << "DYNAMIC PASS visible_selected_id=" << selected_id
              << " successor_created_after_visibility=1 terminal=0\n";
}
std::vector<std::pair<uint64_t, unsigned>> arbitration(const Document& d, bool ir_path) {
    Fixture f;
    std::vector<std::pair<uint64_t, unsigned>> grants;
    if (ir_path) {
        Executor ex(contract(d), f.rt, [&] {
            return f.now;
        });
        auto state = StateSnapshot::initial(7, "search-state", nullptr);
        std::vector<Lease> leases;
        // Same-time independent ready tasks sharing the same resource admission FIFO.
        for (unsigned i = 1; i <= 4; ++i) {
            auto op = d.operations[0];
            op.op_id = i;
            auto a = f.a(ready(state), i == 1 ? 10 : 0);
            a.grant = [&, i](auto id) {
                grants.emplace_back(f.now, i);
                f.rt.start(id);
            };
            leases.push_back(ex.submit(op, {}, std::move(a)));
        }
        for (auto lease : leases)
            ex.release(lease);
        f.empty();
    } else {
        std::vector<runtime::Handle> handles;
        for (unsigned i = 1; i <= 4; ++i) {
            runtime::Operation op;
            op.query = 7;
            op.grant = [&, i](auto id) {
                grants.emplace_back(f.now, i);
                f.rt.start(id);
            };
            op.evaluate = [](const auto&) {
                return content::Bytes{};
            };
            op.service = [&, i](auto id) {
                f.bridge.enqueue(f.now + (i == 1 ? 10 : 0),
                                 EventBridge::ResourceCompleteRelease,
                                 "direct resource",
                                 [&, id] {
                                     f.rt.service_done(id);
                                 });
            };
            handles.push_back(f.rt.submit(std::move(op)));
        }
        for (auto h : handles)
            f.rt.release(h);
        f.empty();
    }
    return grants;
}
void drain_failure(const Document& d) {
    Fixture f;
    Executor ex(contract(d), f.rt, [&] {
        return f.now;
    });
    auto a = f.a(ready(StateSnapshot::initial(7, "search-state", nullptr)));
    a.service = [&](auto id) {
        f.bridge.enqueue(
            10, EventBridge::ResourceCompleteRelease, "injected drained failure", [&, id] {
                (void)id;
                ex.fail_after_drain(
                    1, content::Code::BackendFailure, "injected drained adapter failure");
            });
    };
    auto root = ex.submit(d.operations[0], {}, std::move(a));
    auto child = ex.submit(d.operations[1], {root}, f.a());
    bool observed = false;
    ex.when_all({child}, 1, [&](runtime::Outcome o) {
        check(o.failure && o.failure->code == content::Code::BackendFailure && f.now == 10 &&
                  Json::parse(o.failure->detail)["ir_op"] == 1 &&
                  Json::parse(o.failure->detail)["time_ns"] == 10,
              "drained failure root/time lost");
        observed = true;
    });
    ex.release(root);
    ex.release(child);
    f.empty();
    check(observed && ex.counts().failed == 2 && !ex.counts().active_ops, "drained failure leak");
    std::cout << "DRAIN_FAILURE PASS terminal_ops=0 terminal_tasks=0 terminal_waiters=0\n";
}

void state_ownership(const Document& d) {
    Fixture f;
    Executor ex(contract(d), f.rt, [&] {
        return f.now;
    });
    auto original = StateSnapshot::initial(7, "search-state", {{"expanded", Json::array()}});
    auto next = StateSnapshot::advance(original, {{"expanded", Json::array({1u})}});
    auto next_again = StateSnapshot::advance(original, {{"expanded", Json::array({2u})}});
    check(next_again->identity().version == 3, "state lineage reused version");
    std::weak_ptr<const StateSnapshot> old_weak = original, new_weak = next;
    auto q = ex.submit(d.operations[0], {}, f.a(ready(original), 10));
    auto score = ex.submit(d.operations[1],
                           {q},
                           f.a({{"scores", {Json::array({{{"id", 0u}, {"distance", 1.}}}), {}}}}));
    auto merge = ex.submit(d.operations[2], {score}, f.a({{"scores", {Json::array(), {}}}}));
    auto old_op = d.operations[3];
    rejects("VersionMismatch", [&] {
        ex.submit(old_op, {merge}, f.a());
    });
    rejects("VersionMismatch", [&] {
        ex.submit(old_op, {merge}, f.a(), {{"expanded", next}});
    });
    auto a = f.a();
    a.evaluate = [](const Values& values) {
        check(values.at("expanded").state->identity().version == 1 &&
                  values.at("expanded").state->data()["expanded"].empty(),
              "old snapshot overwritten");
        return Values{{"ids", {Json::array(), {}}}};
    };
    auto old_consumer = ex.submit(old_op, {merge}, std::move(a), {{"expanded", original}});
    auto new_op = old_op;
    new_op.op_id = 5;
    std::get<StateRef>(new_op.inputs[1].value.reference).version = 2;
    auto b = f.a();
    b.evaluate = [](const Values& values) {
        check(values.at("expanded").state->identity().version == 2 &&
                  values.at("expanded").state->data()["expanded"] == Json::array({1u}),
              "new snapshot invalid");
        return Values{{"ids", {Json::array(), {}}}};
    };
    auto new_consumer = ex.submit(new_op, {merge}, std::move(b), {{"expanded", next}});
    original.reset();
    next.reset();
    next_again.reset();
    check(!old_weak.expired() && !new_weak.expired(), "in-flight state lost");
    for (auto lease : {q, score, merge, old_consumer, new_consumer})
        ex.release(lease);
    f.empty();
    check(old_weak.expired() && new_weak.expired() && ex.counts().active_ops == 0,
          "state snapshots leaked");
    std::cout << "STATE_OWNERSHIP PASS old/new immutable versions=1/2 lineage_next=3 "
                 "terminal_snapshots=0\n";
}

void stalled(const Document& d) {
    Fixture f;
    Executor ex(contract(d), f.rt, [&] {
        return f.now;
    });
    uint64_t task = 0;
    auto a = f.a(ready(StateSnapshot::initial(7, "search-state", nullptr)));
    a.grant = [&](auto id) {
        task = id;
    };
    auto h = ex.submit(d.operations[0], {}, std::move(a));
    try {
        f.drain();
        throw std::runtime_error("stall not detected");
    } catch (const std::logic_error&) {
    }
    bool diagnosed = false;
    try {
        ex.require_progress();
    } catch (const std::logic_error& e) {
        std::string text = e.what();
        check(text.find("ir_op=1 task=") != text.npos && text.find("query=7") != text.npos &&
                  text.find("awaiting grant") != text.npos &&
                  text.find("dependencies=") != text.npos,
              "missing IR stall context");
        diagnosed = true;
    }
    check(diagnosed && task, "missing stall");
    ex.fail_after_drain(1, content::Code::DependencyFailed, "cancel stranded adapter");
    ex.release(h);
    f.empty();
    check(ex.counts().active_ops == 0, "stalled records leaked");
    std::cout << "STALL PASS query/IR_op/task/dependencies/resource context terminal=0\n";
}
int main(int argc, char** argv) {
    try {
        if (argc != 2)
            return 2;
        auto d = parse(read_json(argv[1]));
        for (auto delays :
             std::vector<std::vector<uint64_t>>{{0, 0, 0}, {100, 0, 0}, {0, 100, 0}, {0, 0, 100}}) {
            Fixture f;
            Scenario s(
                d,
                f.rt,
                f.bridge,
                [&] {
                    return f.now;
                },
                delays[0],
                delays[1],
                delays[2]);
            f.drain();
            s.finish(65 + delays[0] + delays[1] + delays[2]);
            f.empty();
        }
        stalled(d);
        state_ownership(d);
        guards(d);
        async_and_growth(d);
        dynamic_successor(d, 1);
        dynamic_successor(d, 2);
        drain_failure(d);
        auto direct_order = arbitration(d, false), ir_order = arbitration(d, true);
        check(direct_order == ir_order &&
                  ir_order ==
                      std::vector<std::pair<uint64_t, unsigned>>{{0, 1}, {0, 2}, {0, 3}, {0, 4}},
              "same-time resource FIFO changed");
        std::cout << "ARBITRATION PASS direct=ir grants=(0:1,0:2,0:3,0:4)\n";
        dynamic_failure(d, false);
        dynamic_failure(d, true);
        dynamic_failure(d, false, true);
        std::cout << "V03-2 PASS\n";
    } catch (const std::exception& e) {
        std::cerr << "FAIL " << e.what() << "\n";
        return 1;
    }
}
