#include "search/ir_graph.h"
#include <algorithm>
#include <iostream>
#include <queue>
using namespace ndpsim;
using namespace ndpsim::ir;
void require(bool ok, const char* reason) {
    if (!ok)
        throw std::logic_error(reason);
}
// Controlled service boundaries, not hardware calibration or a replacement mapping.
// The production frontend is unchanged; this trusted test adapter supplies real values.
void run(Document contract, uint64_t input, uint64_t compute, uint64_t ret) {
    Simulator->Reset();
    Simulator->Initialize_simulation();
    contract.operations.clear();
    uint64_t now = 0, seq = 0, next = 0, first_read = UINT64_MAX, independent = UINT64_MAX;
    unsigned probes = 0, decisions = 0;
    bool done = false, first = true;
    using Wake = std::tuple<uint64_t, uint64_t, uint64_t>;
    std::priority_queue<Wake, std::vector<Wake>, std::greater<Wake>> wakes;
    EventBridge bridge(
        [&] {
            return now;
        },
        [&](auto d, auto g) {
            wakes.emplace(now + d, seq++, g);
        });
    runtime::Runtime rt(bridge, [&] {
        return now;
    });
    Executor ex(contract, rt, [&] {
        return now;
    });
    auto state = StateSnapshot::initial(
        7, "search-state", {{"scored", Json::array()}, {"expanded", Json::array()}});
    auto values = [](const Value& v) {
        std::vector<operators::Score> out;
        for (const auto& s : v.data)
            out.push_back({s.at("id").get<uint32_t>(), s.at("distance").get<double>()});
        return out;
    };
    auto submit = [&](Operation op, std::vector<Lease> producers, States states) {
        bool delayed = op.op == Operator::ScoreL2 && first;
        if (delayed)
            first = false;
        Adapter a;
        a.grant = [&](auto task) {
            rt.start(task);
        };
        a.evaluate = [&, op](const Values& in) -> Values {
            switch (op.op) {
            case Operator::QueryReady:
                return {{"query", {Json::array({0., 0., 0., 0.}), {}}},
                        {"state", {nullptr, state}},
                        {"ready", {nullptr, {}}}};
            case Operator::ScoreL2: {
                Json scores = Json::array();
                const float vectors[] = {10, 1, 3, 2};
                for (uint32_t id : in.at("ids").data.get<std::vector<uint32_t>>()) {
                    auto r = operators::squared_l2(operators::fp32({0, 0, 0, 0}),
                                                   {operators::fp32({vectors[id], 0, 0, 0})},
                                                   {id});
                    auto s = operators::scores(r.bytes).at(0);
                    scores.push_back({{"id", s.id}, {"distance", s.distance}});
                }
                return {{"scores", {scores, {}}}};
            }
            case Operator::MergeCandidates: {
                auto scores = values(in.at("previous")), batch = values(in.at("batch"));
                scores.insert(scores.end(), batch.begin(), batch.end());
                std::sort(scores.begin(), scores.end(), [](auto a, auto b) {
                    return std::tie(a.distance, a.id) < std::tie(b.distance, b.id);
                });
                Json out = Json::array();
                for (auto s : scores)
                    out.push_back({{"id", s.id}, {"distance", s.distance}});
                return {{"scores", {out, {}}}};
            }
            case Operator::SelectFrontier: {
                auto ranked = values(in.at("scores"));
                auto expanded =
                    in.at("expanded").state->data().at("expanded").get<std::vector<uint32_t>>();
                std::vector<uint32_t> chosen;
                for (size_t i = 0; i < std::min<size_t>(2, ranked.size()); ++i)
                    if (expanded.size() + chosen.size() < 2 && chosen.empty() &&
                        std::find(expanded.begin(), expanded.end(), ranked[i].id) == expanded.end())
                        chosen.push_back(ranked[i].id);
                return {{"ids", {chosen, {}}}};
            }
            case Operator::ReadAdjacency: {
                first_read = std::min(first_read, now);
                Json out = Json::array();
                for (uint32_t id : in.at("ids").data.get<std::vector<uint32_t>>()) {
                    std::vector<uint32_t> edges =
                        id == 0 ? std::vector<uint32_t>{1, 2} : std::vector<uint32_t>{3};
                    out.push_back({{"degree", edges.size()}, {"ids", edges}});
                }
                return {{"adjacency", {out, {}}}};
            }
            case Operator::DedupNeighbors: {
                auto scored =
                    in.at("scored").state->data().at("scored").get<std::vector<uint32_t>>();
                std::set<uint32_t> fresh;
                for (const auto& row : in.at("adjacency").data)
                    for (uint32_t id : row.at("ids").get<std::vector<uint32_t>>())
                        if (std::find(scored.begin(), scored.end(), id) == scored.end())
                            fresh.insert(id);
                return {{"ids", {std::vector<uint32_t>(fresh.begin(), fresh.end()), {}}}};
            }
            default:
                throw std::logic_error("unexpected causal operator");
            }
        };
        a.service = [&, delayed](auto task) {
            bridge.enqueue(now + (delayed ? 10 + input + compute : 0),
                           EventBridge::ResourceCompleteRelease,
                           "controlled service",
                           [&, task] {
                               rt.service_done(task);
                           });
        };
        a.needs_return = delayed;
        a.send_return = [&](auto task) {
            bridge.enqueue(now + 30 + ret,
                           EventBridge::ResourceCompleteRelease,
                           "controlled return",
                           [&, task] {
                               rt.return_done(task);
                           });
        };
        auto lease = ex.submit(op, std::move(producers), a, std::move(states));
        if (delayed) {
            auto observed = ex.retain(lease);
            for (auto time : {9 + input, 10 + input + compute, 39 + input + compute + ret})
                bridge.enqueue(
                    time, EventBridge::ContinueSubmit, "frontend early probe", [&, observed] {
                        try {
                            ex.get(observed, "scores");
                            throw std::logic_error("early frontend value");
                        } catch (const content::Error& e) {
                            require(e.code == content::Code::NotReady,
                                    "unexpected visibility error");
                        }
                        if (++probes == 3)
                            ex.release(observed);
                    });
        }
        return lease;
    };
    search::IrGraphSearch frontend(
        ex,
        submit,
        next,
        contract.manifest.run,
        7,
        0,
        search::Config{4, 0, 1, 2, 1, 2, 2},
        4,
        4,
        Placement::Host,
        state,
        [&] {
            return now;
        },
        [&](const search::Decision&) {
            ++decisions;
        },
        [&](auto scores, auto expanded) {
            require(scores.at(0).id == 1 && expanded == 2, "wrong causal search result");
            done = true;
        });
    frontend.start();
    // Independent query uses the same executor and EventBridge, completing at 7 ns.
    Operation o{};
    o.op_id = ++next;
    o.scope = {contract.manifest.run, 8, 1, 0, 0, {}, {}};
    o.kind = Kind::External;
    o.op = Operator::QueryReady;
    o.operator_version = 1;
    o.placement = Placement::Host;
    o.outputs = {{"query", {TypeName::Fp32, {{4, false}}}, Location::HostMemory},
                 {"state", {TypeName::QueryState, {}}, Location::HostMemory},
                 {"ready", {TypeName::Token, {}}, Location::HostMemory}};
    o.params = Json::object();
    o.workload = {{"rule", "none"}};
    o.effects = {"pure_value"};
    Adapter a;
    a.grant = [&](auto task) {
        rt.start(task);
    };
    a.evaluate = [](const Values&) {
        return Values{{"query", {Json::array({0., 0., 0., 0.}), {}}},
                      {"state", {nullptr, StateSnapshot::initial(8, "search-state", nullptr)}},
                      {"ready", {nullptr, {}}}};
    };
    a.service = [&](auto task) {
        bridge.enqueue(7, EventBridge::ResourceCompleteRelease, "independent service", [&, task] {
            rt.service_done(task);
        });
    };
    auto independent_lease = ex.submit(o, {}, a);
    ex.when_all({independent_lease}, 8, [&, independent_lease](auto outcome) {
        require(!outcome.failure, "independent failed");
        independent = now;
        ex.release(independent_lease);
    });
    bridge.start();
    while (!wakes.empty()) {
        auto [t, s, g] = wakes.top();
        wakes.pop();
        now = t;
        bridge.wake(g);
    }
    require(done && decisions == 3 && probes == 3 && first_read == 40 + input + compute + ret &&
                independent == 7,
            "causal timing failed");
    require(!ex.counts().active_ops && !ex.counts().active_values && !rt.task_records() &&
                !rt.wait_records(),
            "causal lifetime leak");
    std::cout << "FRONTEND_CAUSAL input=" << input << " compute=" << compute << " return=" << ret
              << " read=" << first_read << " independent=" << independent << " live=0\n";
}
int main(int argc, char** argv) {
    require(argc == 2, "contract required");
    auto d = parse(read_json(argv[1]));
    for (auto delays : {std::array<uint64_t, 3>{0, 0, 0}, {100, 0, 0}, {0, 100, 0}, {0, 0, 100}})
        run(d, delays[0], delays[1], delays[2]);
}
