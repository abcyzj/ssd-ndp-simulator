#include "backend/ordinary_device.h"
#include "ir/mapping.h"
#include "search/graph.h"
#include <iostream>
#include <queue>
using namespace ndpsim;
using namespace ndpsim::ir;
void check(bool ok, const char* why) {
    if (!ok)
        throw std::logic_error(why);
}
struct Node {
    Lease lease;
    Operation op;
};
// Test-only dynamic driver. Production GraphSearch and engine selection are V03-4.
struct Driver {
    Mapping& mapping;
    Executor& ex;
    Document contract;
    std::vector<Operation> templates;
    uint64_t next = 0;
    Node ready, all;
    bool has_all = false;
    std::set<uint32_t> expanded, scored_ids;
    std::shared_ptr<const StateSnapshot> state;
    std::function<void(Json)> decision;
    std::function<void(Json)> done;
    Operation op(size_t index) {
        auto out = templates.at(index);
        out.op_id = ++next;
        out.scope.query = 7;
        out.deps.clear();
        out.inputs.clear();
        out.access.clear();
        if (index == 0)
            out.outputs[0].type = {TypeName::Fp32, {{260, false}}};
        else if (index == 1) {
            out.params = {{"dimensions", 260u}, {"chunk", 2u}};
            out.outputs[0].type = {TypeName::ScoresIdF64, {{5, true}}};
        } else if (index == 2)
            out.outputs[0].type = {TypeName::ScoresIdF64, {{5, true}}};
        else if (index == 3) {
            out.params = {{"L", 5u}, {"B", 2u}, {"E", 5u}};
            out.outputs[0].type = {TypeName::IdsU32, {{2, true}}};
        } else if (index == 4)
            out.outputs[0].type = {TypeName::Adjacency, {{2, true}}};
        else if (index == 5)
            out.outputs[0].type = {TypeName::IdsU32, {{5, true}}};
        if (index == 1)
            out.placement = contract.manifest.mapping["placement"] == "host"
                                ? Placement::Host
                                : Placement::Controller;
        return out;
    }
    OutputRef ref(const Node& n, const std::string& port) {
        for (const auto& p : n.op.outputs)
            if (p.name == port)
                return {n.op.op_id, port, p.type};
        throw std::logic_error("missing test port");
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
        auto h = mapping.submit(o, std::move(producers), std::move(states));
        try {
            ex.get(h, o.outputs.at(0).name);
            throw std::logic_error("mapped output visible inline");
        } catch (const content::Error& e) {
            check(e.code == content::Code::NotReady, "unexpected early output error");
        }
        return {h, o};
    }
    void await(Node n, std::function<void()> callback) {
        ex.when_all({n.lease}, n.op.op_id, [callback](auto outcome) {
            check(!outcome.failure, "driver operation failed");
            callback();
        });
    }
    void update() {
        state = StateSnapshot::advance(
            state,
            {{"scored", std::vector<uint32_t>(scored_ids.begin(), scored_ids.end())},
             {"expanded", std::vector<uint32_t>(expanded.begin(), expanded.end())}});
    }
    void start() {
        state = StateSnapshot::initial(
            7, "search-state", {{"scored", Json::array()}, {"expanded", Json::array()}});
        mapping.admit(7, std::vector<float>(260, 0), state);
        ready = submit(op(0), {});
        score({0}, {});
    }
    void score(const std::vector<uint32_t>& ids, std::optional<Node> source) {
        for (auto id : ids)
            scored_ids.insert(id);
        update();
        auto s = op(1);
        input(s, "query", ready, "query");
        s.deps.push_back({ready.op.op_id, DependencyKind::Control, "ready", 0});
        std::vector<Lease> deps{ready.lease};
        Slice access{"base", "vector", 1, std::vector<uint32_t>{0}, 0, 0};
        if (source) {
            input(s, "ids", *source, "ids", DependencyKind::Address);
            access.indices = ref(*source, "ids");
            deps.push_back(source->lease);
            s.outputs[0].type.shape = source->op.outputs[0].type.shape;
        } else {
            s.inputs.push_back(
                {"ids", {{TypeName::IdsU32, {{1, false}}}, Literal{std::vector<uint32_t>{0}}}});
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
            m.outputs[0].type.shape = {{1, true}};
        }
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
            auto ids = ex.get(selected.lease, "ids").data.get<std::vector<uint32_t>>();
            decision({{"scores", ex.get(all.lease, "scores").data}, {"ids", ids}});
            if (ids.empty()) {
                done(ex.get(all.lease, "scores").data);
                ex.release(selected.lease);
                ex.release(all.lease);
                ex.release(ready.lease);
                return;
            }
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
                auto ids = ex.get(fresh.lease, "ids").data.get<std::vector<uint32_t>>();
                if (ids.empty()) {
                    ex.release(fresh.lease);
                    select();
                } else
                    score(ids, fresh);
            });
        });
    }
};
struct Scenario {
    uint64_t now = 0, sequence = 0;
    using Wake = std::tuple<uint64_t, uint64_t, uint64_t>;
    std::priority_queue<Wake, std::vector<Wake>, std::greater<Wake>> wakes;
    OrdinaryDevice device{128};
    EventBridge bridge{[this] {
                           return now;
                       },
                       [this](auto d, auto g) {
                           wakes.emplace(now + d, sequence++, g);
                       }};
    runtime::Runtime rt{bridge, [this] {
                            return now;
                        }};
    model::HostCpu cpu{rt,
                       bridge,
                       [this] {
                           return now;
                       },
                       model::HostConfig{1, {3, 13}, {2, 1}, 1, 1, "configured_unvalidated"}};
    std::unique_ptr<content::ContentStore> store;
    std::unique_ptr<content::Layout> layout;
    std::unique_ptr<content::Handles> handles;
    std::unique_ptr<LocalReads> local;
    transport::PcieTransport transport{*device.link, bridge, [this] {
                                           return now;
                                       }};
    std::unique_ptr<model::Controller> controller;
    std::unique_ptr<Executor> executor;
    std::unique_ptr<Mapping> mapping;
    std::unique_ptr<Driver> driver;
    std::unique_ptr<search::GraphSearch> direct;
    struct Read {
        uint64_t task;
        content::Handle handle;
        std::unique_ptr<content::ReadAssociation> association;
    };
    std::map<uint64_t, std::shared_ptr<Read>> requests;
    Json report = {{"reads", Json::array()}, {"decisions", Json::array()}};
    uint64_t origin = 0, dma = 0;
    bool begun = false, done = false, closed = false;
    std::string engine, placement, mode;
    bool fault;
    Scenario(std::string e, std::string p, const Document& example, std::string m)
        : engine(e), placement(p), mode(m), fault(m == "failure" || m == "openfailure") {
        std::vector<content::Input> inputs;
        std::vector<content::Placement> placements;
        std::vector<std::vector<uint32_t>> graph{{1, 2, 3, 4}, {3}, {4}, {2}, {1}};
        std::vector<float> values{10, -1, 1, 0, 2};
        for (uint32_t i = 0; i < 5; ++i) {
            content::ObjectRef v{1, content::Kind::Vector, i, 1},
                a{1, content::Kind::Adjacency, i, 1};
            inputs.push_back({v, operators::fp32(std::vector<float>(260, values[i]))});
            auto row = graph[i];
            row.insert(row.begin(), row.size());
            inputs.push_back({a, search::ids_bytes(row)});
            placements.push_back({v, uint64_t(i * 16 + 7) * 512});
            placements.push_back({a, uint64_t(i * 16 + 12) * 512});
        }
        store = std::make_unique<content::ContentStore>(inputs);
        layout = std::make_unique<content::Layout>(*store, 128, placements);
        handles = std::make_unique<content::Handles>(*store);
        auto* host =
            static_cast<SSD_Components::Host_Interface_NVMe*>(device.device->Host_interface);
        local = std::make_unique<LocalReads>(*host, bridge, [this] {
            return now;
        });
        if (p == "controller") {
            model::ControllerConfig c;
            c.dimension = 260;
            c.chunk_limit = 2;
            c.max_inflight = 1;
            c.vector_sector_bound = 1536;
            c.scratch_bytes = 16;
            c.capacity_bytes = mode == "tight" ? 2672 : 5264;
            if (mode == "oversize")
                c.vector_sector_bound = 512;
            c.launch_ns = 11;
            c.flops_per_ns = 3;
            controller = std::make_unique<model::Controller>(
                rt,
                *handles,
                *layout,
                *local,
                transport,
                bridge,
                [this] {
                    return now;
                },
                c,
                [this](const std::string& stage, content::Context) {
                    if (fault && stage == (mode == "openfailure" ? "open-command" : "evaluate")) {
                        fault = false;
                        throw content::Error(content::Code::InvalidArgument,
                                             "injected mapping child failure");
                    }
                });
        }
        device.link->observe = [this](const char* phase,
                                      const Host_Components::PCIe_Link::Transfer& t) {
            if (begun)
                report["link"].push_back({phase,
                                          unsigned(t.type),
                                          unsigned(t.destination),
                                          t.payload,
                                          t.wire,
                                          t.service,
                                          t.time - origin});
        };
        if (mode == "rival")
            cpu.observations().sink = [this](const auto& service) {
                if (service.kind != model::Kind::Distance)
                    return;
                bridge.enqueue(service.end_ns,
                               EventBridge::Publish,
                               "same-time independent CPU contender",
                               [this] {
                                   auto h =
                                       cpu.submit(99, {}, model::Kind::Control, [](const auto&) {
                                           operators::Result r;
                                           r.work.comparisons = 50;
                                           return r;
                                       });
                                   rt.release(h);
                               });
            };
        device.root->data_arrived = [this](auto a, auto n) {
            if (begun)
                dma += n;
            device.flow->data(a, n);
        };
        device.flow->completed = [this](auto c) {
            if (!begun)
                return;
            auto s = requests.at(c.token);
            s->association->complete(c.token, c.lba, c.sectors, c.write);
            requests.erase(c.token);
            if (s->association->complete()) {
                handles->publish(s->handle);
                rt.start(s->task);
            }
        };
        if (e == "ir") {
            auto doc = example;
            doc.operations.clear();
            doc.manifest.mapping["placement"] = p;
            doc.manifest.artifacts["dataset"]["shape"] = {5u, 260u};
            doc.manifest.artifacts["queries"]["shape"] = {4u, 260u};
            doc.manifest.artifacts["index"]["shape"] = {5u, 4u};
            doc.manifest.artifacts["truth"]["shape"] = {4u, 5u};
            doc.manifest.search = {{"strategy", "ndpsim-ranked-expansion-v1"},
                                   {"entry", 0u},
                                   {"k", 5u},
                                   {"L", 5u},
                                   {"B", 2u},
                                   {"E", 5u},
                                   {"chunk", 2u}};
            for (auto& o : doc.objects) {
                o.width = o.object_type == "adjacency" ? 4 : 260;
                o.element_count = o.object_type == "query_vectors" ? 4 : 5;
            }
            executor = std::make_unique<Executor>(doc, rt, [this] {
                return now;
            });
            mapping = std::make_unique<Mapping>(
                doc,
                *executor,
                rt,
                cpu,
                *store,
                *layout,
                [this](auto q, const auto& s) {
                    return read(q, s);
                },
                std::vector<Mapping::ObjectBinding>{{"base", 1, 1, content::Kind::Vector},
                                                    {"graph", 1, 1, content::Kind::Adjacency}},
                controller.get());
            driver = std::make_unique<Driver>(Driver{*mapping, *executor, doc, example.operations});
            driver->decision = [this](auto d) {
                d["time"] = now - origin;
                report["decisions"].push_back(d);
            };
            driver->done = [this](auto s) {
                finish(s);
            };
        }
    }
    runtime::Handle read(uint64_t q, const content::Selection& selection) {
        if (mode == "partial" && selection.object.kind == content::Kind::Vector &&
            selection.object.id == 2)
            throw content::Error(content::Code::InvalidArgument,
                                 "injected synchronous read adapter failure");
        auto s = std::make_shared<Read>();
        runtime::Operation op;
        op.query = q;
        op.grant = [this, s, q, selection](auto task) {
            s->task = task;
            s->association = std::make_unique<content::ReadAssociation>(
                *layout,
                std::vector<content::Selection>{selection},
                content::Route::Ordinary,
                content::Location::HostMemory,
                content::Context{q, task, now});
            s->handle = handles->create({selection}, content::Location::HostMemory, {q, task, now});
            const auto& plan = s->association->plan();
            for (size_t i = 0; i < plan.extents.size(); ++i) {
                auto ex = plan.extents[i];
                auto token = device.window->read(ex.lba, ex.sectors);
                s->association->bind(i, token);
                requests.emplace(token, s);
                report["reads"].push_back({{"time", now - origin},
                                           {"kind", unsigned(selection.object.kind)},
                                           {"id", selection.object.id},
                                           {"lba", ex.lba},
                                           {"sectors", ex.sectors}});
            }
        };
        op.evaluate = [this, s](const auto&) {
            auto bytes = handles->try_get(s->handle, content::Consumer::HostExecutor, 1).at(0);
            handles->release(s->handle);
            return bytes;
        };
        op.service = [this](auto task) {
            rt.service_done(task);
        };
        return rt.submit(op);
    }
    void finish(Json scores) {
        done = true;
        report["scores"] = scores;
        report["end"] = now - origin;
        if (mapping) {
            auto h = mapping->close(7);
            rt.when_all({h}, 7, [this, h](auto o) {
                check(!o.failure, "mapping close failed");
                rt.release(h);
                closed = true;
            });
        } else if (controller) {
            auto h = controller->close(7);
            rt.when_all({h}, 7, [this, h](auto o) {
                check(!o.failure, "direct close failed");
                rt.release(h);
                closed = true;
            });
        } else
            closed = true;
    }
    void boundary() {
        driver->state = StateSnapshot::initial(
            7, "search-state", {{"scored", Json::array()}, {"expanded", Json::array()}});
        mapping->admit(7, std::vector<float>(260, 0), driver->state);
        auto bad = driver->op(0);
        bad.scope.query = 8;
        try {
            mapping->submit(bad, {});
            throw std::logic_error("unadmitted query accepted");
        } catch (const ir::Error& e) {
            check(e.query == 8 && e.op == bad.op_id && e.time_ns == now,
                  "missing admission error context");
        }
        auto ready = driver->submit(driver->op(0), {});
        auto duplicate = driver->op(0);
        auto before = rt.task_records();
        try {
            mapping->submit(duplicate, {});
            throw std::logic_error("duplicate QUERY_READY accepted");
        } catch (const ir::Error& e) {
            check(e.op == duplicate.op_id && rt.task_records() == before,
                  "duplicate ready allocated work / lost context");
        }

        auto s = driver->op(1);
        driver->input(s, "query", ready, "query");
        s.deps.push_back({ready.op.op_id, DependencyKind::Control, "ready", 0});
        std::vector<uint32_t> ids = mode == "duplicate" ? std::vector<uint32_t>{0, 1, 1}
                                                        : std::vector<uint32_t>{0, 1, 2, 3};
        ir::Slice access{"base", "vector", 1, ids, 0, 0};
        s.inputs.push_back({"ids", {{TypeName::IdsU32, {{ids.size(), false}}}, access}});
        s.access = {access};
        s.outputs[0].type.shape = {{ids.size(), false}};
        auto scored = driver->submit(s, {ready.lease});
        auto join = driver->op(6);
        driver->input(join, "scores", scored, "scores");
        auto joined = driver->submit(join, {scored.lease});
        executor->when_all({joined.lease}, join.op_id, [this, joined](auto o) {
            check(bool(o.failure) == (mode == "failure" || mode == "duplicate" ||
                                      mode == "partial" || mode == "oversize" ||
                                      mode == "openfailure"),
                  "failure did not propagate to JOIN");
            executor->release(joined.lease);
        });
        const bool failure = mode == "failure" || mode == "duplicate" || mode == "partial" ||
                             mode == "oversize" || mode == "openfailure";
        executor->when_all({scored.lease}, s.op_id, [this, scored, ready, failure](auto o) {
            check(bool(o.failure) == failure, "wrong mapping failure outcome");
            if (o.failure) {
                if (controller)
                    check(!controller->pending_chunks(7),
                          "parent failed before sibling chunks drained");
                check(o.failure->detail.find("op_id") != std::string::npos ||
                          o.failure->detail.find("ir_op") != std::string::npos,
                      "failure lost IR context");
                report["failure"] = o.failure->detail;
            } else {
                auto scores = executor->get(scored.lease, "scores").data;
                check(scores.size() == 4 && scores[3]["id"] == 3u, "ordered batch missing chunk");
                report["scores"] = scores;
            }
            report["end"] = now - origin;
            done = true;
            executor->release(scored.lease);
            executor->release(ready.lease);
        });
        // Close before even QUERY_READY has published; every child must drain.
        auto close = mapping->close(7);
        rt.when_all({close}, 7, [this, close](auto o) {
            check(bool(o.failure) == (mode == "openfailure") && done,
                  "early Close lost pending score/drain");
            rt.release(close);
            closed = true;
        });
    }
    void launch() {
        if (driver && !mode.empty() && mode != "tight" && mode != "rival") {
            boundary();
            return;
        }
        if (driver) {
            driver->start();
            return;
        }
        search::GraphSearch::Score score;
        if (controller) {
            auto h = controller->open(7, 1, std::vector<float>(260, 0));
            rt.release(h);
            score = [this](const auto& ids) {
                std::vector<content::Selection> s;
                for (auto id : ids)
                    s.push_back({{1, content::Kind::Vector, id, 1}, {0, 1040}});
                return controller->score(7, s);
            };
        }
        direct = std::make_unique<search::GraphSearch>(
            rt,
            cpu,
            [this] {
                return now;
            },
            7,
            search::Config{5, 0, 5, 5, 2, 5, 2},
            operators::fp32(std::vector<float>(260, 0)),
            [this](auto kind, auto id) {
                content::ObjectRef o{1, kind, id, 1};
                return read(7, {o, {0, store->metadata(o).size}});
            },
            [this](const auto& d) {
                Json scores = Json::array();
                for (auto s : d.candidates)
                    scores.push_back({{"id", s.id}, {"distance", s.distance}});
                report["decisions"].push_back(
                    {{"time", now - origin}, {"scores", scores}, {"ids", d.expand}});
            },
            [this](auto scores, auto) {
                Json s = Json::array();
                for (auto v : scores)
                    s.push_back({{"id", v.id}, {"distance", v.distance}});
                finish(s);
            },
            score);
        direct->start();
    }
    void run() {
        device.start();
        bridge.start();
        unsigned steps = 0;
        while (!wakes.empty()) {
            check(++steps < 200000, "mapping stalled");
            auto [time, seq, g] = wakes.top();
            wakes.pop();
            now = time;
            bridge.wake(g);
            local->poll();
            if (!begun && device.window->advance(bridge.idle(), now)) {
                begun = true;
                origin = now;
                launch();
            }
            rt.require_progress();
        }
        check(done && closed && requests.empty() && !cpu.busy() && !rt.task_records() &&
                  !rt.wait_records() && !handles->live_records(),
              "mapping resources leaked");
        if (executor)
            check(!executor->counts().active_ops && !executor->counts().active_values &&
                      !mapping->sessions(),
                  "IR resources leaked");
        report["drain"] = now - origin;
        report["dma"] = dma;
        report["host"] = Json::array();
        for (auto r : cpu.services()) {
            auto w = r.work;
            report["host"].push_back({r.submitted_ns - origin,
                                      r.start_ns - origin,
                                      r.end_ns - origin,
                                      r.service_ns,
                                      r.ready_ns - origin,
                                      unsigned(r.kind),
                                      w.elements,
                                      w.flops,
                                      w.read_bytes,
                                      w.write_bytes,
                                      w.output_bytes,
                                      w.candidate_checks,
                                      w.comparisons});
        }
        if (controller) {
            check(!controller->buffer().used() && !controller->sessions() &&
                      !controller->pending_protocol() && !local->pending() && !transport.pending(),
                  "controller not drained");
            report["storage"] = Json::array();
            for (auto r : controller->storage())
                report["storage"].push_back({r.submitted - origin,
                                             r.completed - origin,
                                             r.sector_bytes,
                                             r.requests,
                                             r.transactions});
            report["messages"] = Json::array();
            for (auto r : transport.records())
                report["messages"].push_back({unsigned(r.kind),
                                              r.payload,
                                              r.useful,
                                              r.submitted - origin,
                                              r.arrived - origin});
            report["credits"] = Json::array();
            for (auto r : controller->buffer().records())
                report["credits"].push_back(
                    {r.time - origin, r.bytes, r.used, r.reserve, r.lifetime});
            report["resources"] = Json::array();
            for (auto* resource : {&controller->port(), &controller->compute()})
                for (auto r : resource->records())
                    report["resources"].push_back(
                        {r.stage, r.submitted - origin, r.start - origin, r.end - origin, r.bytes});
        }
        std::cout << "MAPPING_REPORT " << report.dump() << '\n';
    }
};
int main(int argc, char** argv) {
    try {
        check(argc == 4 || argc == 5, "usage: probe contract direct|ir host|controller [boundary]");
        auto d = parse(read_json(argv[1]));
        Scenario scenario(argv[2], argv[3], d, argc == 5 ? argv[4] : "");
        scenario.run();
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
