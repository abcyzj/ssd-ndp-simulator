#pragma once
#include "ir/executor.h"
#include "search/graph.h"

namespace ndpsim::search {
using namespace ir;
// Dynamic frontend: no backend, content, CPU or Controller capability.
class IrGraphSearch {
public:
    using Submit = std::function<Lease(Operation, std::vector<Lease>, States)>;
    IrGraphSearch(Executor& e,
                  Submit submit,
                  uint64_t& ids,
                  std::string run_id,
                  uint64_t q,
                  uint64_t ord,
                  Config c,
                  uint32_t d,
                  uint32_t degree,
                  Placement p,
                  std::shared_ptr<const StateSnapshot> initial,
                  EventBridge::Clock clock,
                  GraphSearch::Trace trace,
                  GraphSearch::Done done)
        : ex(e), submit_(std::move(submit)), next(ids), run(std::move(run_id)), query(q),
          ordinal(ord), cfg(c), dimensions(d), degree_(degree), placement(p),
          state(std::move(initial)), now(std::move(clock)), trace_(std::move(trace)),
          done_(std::move(done)) {
        validate(cfg);
    }
    void start() {
        ready = submit(op(0), {});
        score({cfg.entry}, {});
    }

private:
    struct Node {
        Lease lease;
        Operation op;
    };
    Executor& ex;
    Submit submit_;
    uint64_t& next;
    std::string run;
    uint64_t query, ordinal, iteration = 0;
    Config cfg;
    uint32_t dimensions, degree_;
    Placement placement;
    Node ready, all;
    bool has_all = false;
    std::set<uint32_t> expanded, scored_ids;
    std::shared_ptr<const StateSnapshot> state;
    EventBridge::Clock now;
    GraphSearch::Trace trace_;
    GraphSearch::Done done_;
    std::vector<operators::Score> scores() {
        std::vector<operators::Score> out;
        auto value = ex.get(all.lease, "scores");
        if (auto bytes = value.packed_scores())
            return operators::scores(*bytes);
        for (const auto& v : value.data())
            out.push_back({v.at("id").get<uint32_t>(), v.at("distance").get<double>()});
        return out;
    }
    Operation op(size_t index) {
        if (next == UINT64_MAX)
            throw std::overflow_error("IR operation ID exhausted");
        Operation out{};
        out.op_id = ++next;
        out.scope = {run, query, ordinal, iteration, iteration, {}, {}};
        out.operator_version = 1;
        out.placement = Placement::Host;
        out.params = Json::object();
        static const Operator names[] = {Operator::QueryReady,
                                         Operator::ScoreL2,
                                         Operator::MergeCandidates,
                                         Operator::SelectFrontier,
                                         Operator::ReadAdjacency,
                                         Operator::DedupNeighbors};
        static const Kind kinds[] = {Kind::External,
                                     Kind::Compute,
                                     Kind::Control,
                                     Kind::Control,
                                     Kind::Memory,
                                     Kind::Control};
        static const char* rules[] = {"none",
                                      "ordered-fp64-l2-v1",
                                      "candidates-v1",
                                      "select-frontier-v1",
                                      "none",
                                      "dedup-neighbors-v1"};
        static const char* effects[] = {"pure_value",
                                        "logical_read",
                                        "query_state_transition",
                                        "query_state_transition",
                                        "logical_read",
                                        "pure_value"};
        out.op = names[index];
        out.kind = kinds[index];
        out.workload = {{"rule", rules[index]}};
        out.effects = {effects[index]};
        if (index == 0)
            out.outputs = {{"query", {TypeName::Fp32, {{dimensions, false}}}, Location::HostMemory},
                           {"state", {TypeName::QueryState, {}}, Location::HostMemory},
                           {"ready", {TypeName::Token, {}}, Location::HostMemory}};
        else {
            const char* port = index == 1 || index == 2 ? "scores"
                               : index == 4             ? "adjacency"
                                                        : "ids";
            TypeName type = index == 1 || index == 2 ? TypeName::ScoresIdF64
                            : index == 4             ? TypeName::Adjacency
                                                     : TypeName::IdsU32;
            out.outputs = {{port,
                            {type, {{index == 3 || index == 4 ? cfg.batch : cfg.count, true}}},
                            Location::HostMemory}};
        }
        if (index == 3)
            out.outputs[0].type.shape = {{std::min(cfg.batch, cfg.budget), true}};
        if (index == 4)
            out.outputs[0].type.shape = {{std::min(cfg.batch, cfg.budget), true}};
        if (index == 5)
            out.outputs[0].type.shape = {
                {std::min<uint64_t>(cfg.count, uint64_t(std::min(cfg.batch, cfg.budget)) * degree_),
                 true}};
        if (index == 1) {
            out.params = {{"dimensions", dimensions}, {"chunk", cfg.chunk}};
            out.placement = placement;
        } else if (index == 3)
            out.params = {{"L", cfg.width}, {"B", cfg.batch}, {"E", cfg.budget}};
        return out;
    }
    OutputRef ref(const Node& n, const std::string& port) {
        for (const auto& p : n.op.outputs)
            if (p.name == port)
                return {n.op.op_id, port, p.type};
        throw std::logic_error("missing graph IR port");
    }
    void input(Operation& o,
               std::string port,
               const Node& n,
               std::string output,
               DependencyKind kind = DependencyKind::Data) {
        auto r = ref(n, output);
        o.inputs.push_back({port, {r.type, r}});
        o.deps.push_back({r.producer_op, kind, r.port, 0});
    }
    void snapshot(Operation& o, const std::string& port) {
        o.inputs.push_back({port, {{TypeName::QueryState, {}}, state->identity()}});
    }
    Node submit(Operation o, std::vector<Lease> producers, States states = {}) {
        auto h = submit_(o, std::move(producers), std::move(states));
        return {h, o};
    }
    void await(Node n, std::function<void()> callback) {
        ex.when_all({n.lease}, n.op.op_id, [callback](auto outcome) {
            if (outcome.failure)
                throw std::runtime_error(outcome.failure->detail);
            callback();
        });
    }
    void update() {
        state = StateSnapshot::advance(
            state,
            {{"scored", std::vector<uint32_t>(scored_ids.begin(), scored_ids.end())},
             {"expanded", std::vector<uint32_t>(expanded.begin(), expanded.end())}});
    }
    void score(const std::vector<uint32_t>& ids, std::optional<Node> source) {
        for (auto id : ids)
            scored_ids.insert(id);
        update();
        auto s = op(1);
        input(s, "query", ready, "query");
        s.deps.push_back({ready.op.op_id, DependencyKind::Control, "ready", 0});
        std::vector<Lease> deps{ready.lease};
        Slice access{"base", "vector", 1, std::vector<uint32_t>{cfg.entry}, 0, 0};
        if (source) {
            input(s, "ids", *source, "ids", DependencyKind::Address);
            access.indices = ref(*source, "ids");
            deps.push_back(source->lease);
            s.outputs[0].type.shape = source->op.outputs[0].type.shape;
        } else {
            s.inputs.push_back(
                {"ids",
                 {{TypeName::IdsU32, {{1, false}}}, Literal{std::vector<uint32_t>{cfg.entry}}}});
            s.outputs[0].type.shape = {{1, false}};
        }
        s.access = {access};
        auto scores = submit(s, deps);
        if (source)
            ex.release(source->lease);
        auto m = op(2);
        input(m, "batch", scores, "scores");
        deps = {scores.lease};
        if (has_all) {
            input(m, "previous", all, "scores");
            deps.push_back(all.lease);
        } else {
            m.inputs.push_back(
                {"previous",
                 {{TypeName::ScoresIdF64, {{0, false}}}, Literal{std::vector<uint32_t>{}}}});
        }
        m.outputs[0].type.shape = {
            {std::min<uint64_t>(cfg.count,
                                scores.op.outputs[0].type.shape[0].extent +
                                    (has_all ? all.op.outputs[0].type.shape[0].extent : 0)),
             true}};
        auto merged = submit(m, deps);
        ex.release(scores.lease);
        if (has_all)
            ex.release(all.lease);
        all = merged;
        has_all = true;
        await(merged, [this] {
            select();
        });
    }
    void select() {
        auto s = op(3);
        input(s, "scores", all, "scores");
        snapshot(s, "expanded");
        auto selected = submit(s, {all.lease}, {{"expanded", state}});
        await(selected, [this, selected] {
            auto ids = ex.get(selected.lease, "ids").data().get<std::vector<uint32_t>>();
            auto ranked = scores();
            if (ranked.size() > cfg.width)
                ranked.resize(cfg.width);
            trace_({now(), ranked, ids});
            if (ids.empty()) {
                auto result = scores();
                if (result.size() > cfg.k)
                    result.resize(cfg.k);
                ex.release(selected.lease);
                ex.release(all.lease);
                ex.release(ready.lease);
                done_(std::move(result), expanded.size());
                return;
            }
            ++iteration;
            for (auto id : ids)
                expanded.insert(id);
            update();
            auto r = op(4);
            input(r, "ids", selected, "ids", DependencyKind::Address);
            r.deps.push_back({selected.op.op_id, DependencyKind::Control, "ids", 0});
            r.access = {{"graph", "neighbors", 1, ref(selected, "ids"), 0, 0}};
            auto read = submit(r, {selected.lease});
            ex.release(selected.lease);
            auto d = op(5);
            input(d, "adjacency", read, "adjacency");
            snapshot(d, "scored");
            auto fresh = submit(d, {read.lease}, {{"scored", state}});
            ex.release(read.lease);
            await(fresh, [this, fresh] {
                auto ids = ex.get(fresh.lease, "ids").data().get<std::vector<uint32_t>>();
                if (ids.empty()) {
                    ex.release(fresh.lease);
                    select();
                } else
                    score(ids, fresh);
            });
        });
    }
};
} // namespace ndpsim::search
