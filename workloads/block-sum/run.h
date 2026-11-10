#pragma once
#include "contract.h"
#include "dag/executor.h"
#include "model/cost/cost_model.h"
#include "backend/ordinary_device.h"
#include "timing/controller.h"
#include <nlohmann/json.hpp>
#include <deque>
namespace block_sum {
using namespace ndpsim;
using J = nlohmann::json;
inline void need(bool b, const char* message) {
    if (!b)
        throw std::runtime_error(message);
}
inline std::string bytes(const std::filesystem::path& p) {
    std::ifstream f(p, std::ios::binary);
    need(bool(f), "metadata file missing");
    std::string s((std::istreambuf_iterator<char>(f)), {});
    need(s.size() < 16 * 1024 * 1024, "metadata budget");
    return s;
}
class Run {
    EventBridge& bridge;
    EventBridge::Clock now;
    timing::Runtime runtime;
    timing::HostCpu cpu;
    timing::FifoResource endpoint;
    cost::Registry registry;
    cost::Target target;
    std::unique_ptr<cost::Profile> profile;
    std::unique_ptr<dag::Package> package;
    std::unique_ptr<layout::Layout> layout;
    std::unique_ptr<DeviceConfig> config;
    std::unique_ptr<OrdinaryDevice> device;
    timing::MessageRules rules;
    std::unique_ptr<timing::MetadataLocalReads> local;
    std::unique_ptr<timing::MetadataPcie> pcie;
    std::unique_ptr<timing::MetadataController> controller;
    std::unique_ptr<dag::Executor> executor;
    bool offload = false, launched = false, finished_ = false, ok = false, reported = false;
    uint64_t chunk = 0, ordinary_count = 0, local_count = 0, dma = 0, origin = 0;
    std::ofstream trace;
    std::deque<std::pair<layout::Plan, timing::Done>> ordinary;
    std::function<void(ExternalFlow::Completion)> io_done;
    void emit(J j) {
        trace << j.dump() << '\n';
        trace.flush();
        need(bool(trace), "trace write failed");
    }
    timing::Message message(timing::MessageKind k) {
        timing::Message m;
        m.protocol = "example-sum/1";
        m.kind = k;
        m.version = 1;
        if (k == timing::MessageKind::Open)
            m.count = m.dimension = 1;
        return m;
    }
    void await(timing::CompletionToken t, timing::Done done) {
        runtime.when_complete(t, [this, t, done](auto x) {
            runtime.release(t);
            done(x);
        });
    }
    cost::Input input(const dag::Stage& s) {
        need(s.operator_id == "example.BLOCK_SUM" && s.operator_version == "1" &&
                 s.metadata.kind == "sum",
             "unknown extension mapping");
        need(s.metadata.context == "example-readonly/1" &&
                 s.metadata.decomposition_rule == "none/1",
             "unsupported extension context/decomposition");
        const bool join = s.key.op == "join";
        need(join || s.key.op == "left" || s.key.op == "right", "unknown operation");
        capture::Counters expected = {{"elements", {join ? 2U : 4U, "count"}}};
        need(s.metadata.work == expected && s.metadata.features.empty(), "sum Work mismatch");
        need(s.metadata.outputs.size() == 1 && s.metadata.outputs[0].logical_bytes == 8,
             "sum output size");
        need(s.metadata.accesses.size() == (join ? 0U : 1U), "sum access cardinality");
        if (!join) {
            auto a = s.metadata.accesses[0];
            need(a.object == "block" && a.version == 0 &&
                     a.offset_bytes == (s.key.op == "left" ? 0U : 16U) && a.length_bytes == 16,
                 "sum access mismatch");
        }
        cost::Input i;
        i.key = s.key;
        i.operator_id = s.operator_id;
        i.operator_version = s.operator_version;
        i.stage_kind = "sum";
        i.context = s.metadata.context;
        i.location = offload && !join ? "controller" : "host";
        i.work = s.metadata.work;
        i.target = target;
        return i;
    }
    timing::Operation lower(const dag::Stage& s) {
        auto i = input(s);
        auto ns = profile->demand(i);
        emit({{"type", "demand"},
              {"op", s.key.op},
              {"elements", i.work.at("elements").first},
              {"location", i.location},
              {"source", "prediction"},
              {"operator", i.operator_id},
              {"stage_kind", i.stage_kind},
              {"duration_ns", ns},
              {"output_bytes", 8}});
        timing::Operation o;
        o.key = s.key;
        o.outputs = s.metadata.outputs;
        o.work = s.metadata.work;
        o.duration_ns = ns;
        o.resource = &cpu;
        o.service_key = "example-linear/1:" + s.key.op;
        if (s.metadata.accesses.empty()) {
            if (offload)
                o.send_return = [this](auto done) {
                    await(controller->close(0), done);
                };
            return o;
        }
        auto reads = layout->resolve(s.metadata.accesses);
        emit({{"type", "read_plan"},
              {"op", s.key.op},
              {"logical_bytes", reads.logical_bytes},
              {"sector_bytes", reads.sector_bytes}});
        if (!offload) {
            o.input = [this, reads](auto done) {
                bool first = ordinary.empty();
                ordinary.emplace_back(reads, done);
                if (first)
                    next_read();
            };
        } else {
            timing::ControllerRequest r;
            r.command = message(timing::MessageKind::Execute);
            r.command.chunk = ++chunk;
            r.command.count = 4;
            r.command.dimension = 1;
            r.result = r.command;
            r.result.kind = timing::MessageKind::Result;
            r.result.dimension = 0;
            r.reads = reads;
            r.scratch_bytes = 48;
            r.operation = o;
            r.operation.resource = nullptr;
            o.duration_ns = 0;
            o.resource = &endpoint;
            o.input = [this, r](auto done) mutable {
                await(controller->execute(r), done);
            };
        }
        return o;
    }
    void next_read() {
        auto& job = ordinary.front();
        if (job.first.reads.empty()) {
            auto done = job.second;
            ordinary.pop_front();
            bridge.enqueue(now(), EventBridge::ResourceCompleteRelease, "example ordinary", [done] {
                done({});
            });
            if (!ordinary.empty())
                next_read();
            return;
        }
        auto r = job.first.reads.front();
        job.first.reads.erase(job.first.reads.begin());
        ++ordinary_count;
        Simulator->Synchronize_now(now());
        io_done = [this, r](auto c) {
            need(c.lba == r.lba && c.sectors == r.sectors && c.time == now(),
                 "ordinary completion mismatch");
            emit({{"type", "ordinary_read"},
                  {"lba", c.lba},
                  {"sectors", c.sectors},
                  {"completed", c.time}});
            bridge.enqueue(now(), EventBridge::ResourceCompleteRelease, "example CQ", [this] {
                next_read();
            });
        };
        device->flow->submit(r.lba, r.sectors);
    }

public:
    Run(const std::filesystem::path& path, EventBridge& b, EventBridge::Clock n)
        : bridge(b), now(n), runtime(b, n), cpu(b, n, 2), endpoint(b, n, 1) {
        auto c = J::parse(bytes(path));
        need(c.at("format") == "ndpsim-block-sum-run/1" && c.size() == 15 + c.contains("layout"), "example config fields");
        auto resolve = [&](std::string k) {
            return std::filesystem::absolute(path).parent_path() / c.at(k).get<std::string>();
        };
        need(c.at("placement") == "host" || c.at("placement") == "controller", "unknown placement");
        offload = c.at("placement") == "controller";
        auto t = c.at("target");
        target = {t.at("id"),
                  t.at("hardware"),
                  t.at("os"),
                  t.at("frequency"),
                  t.at("affinity"),
                  t.at("memory_bytes")};
        cost::register_builtin_formulas(registry);
        registry.add_operator(contract());
        auto raw = bytes(resolve("profile"));
        const auto profile_descriptor = J::parse(raw);
        for (const auto& rule : profile_descriptor.at("rules"))
            need(rule.at("source") == "prediction", "block-sum requires teaching predictions");
        profile = std::make_unique<cost::Profile>(
            cost::Profile::parse(raw, c.at("profile_sha256"), registry, target, true));
        dag::Receipt receipt;
        receipt.report = resolve("report");
        receipt.report_sha256 = c.at("report_sha256");
        receipt.package_sha256 = c.at("package_sha256");
        receipt.registry_sha256 = c.at("registry_sha256");
        receipt.semantic_report = resolve("semantic_report");
        receipt.semantic_sha256 = c.at("semantic_sha256");
        package = std::make_unique<dag::Package>(resolve("package"), receipt, resolve("scratch"));
        auto manifest = J::parse(bytes(resolve("package") / "manifest.json"));
        std::vector<capture::Object> objects;
        for (auto j : manifest.at("objects"))
            objects.push_back({j.at("id"),
                               j.at("version"),
                               j.at("type"),
                               j.at("size_bytes"),
                               j.at("shape").get<std::vector<uint64_t>>(),
                               ""});
        layout = std::make_unique<layout::Layout>(c.value("layout", "layout.json"), path, objects);
        config = std::make_unique<DeviceConfig>(c.at("device").get<std::string>(), path);
        // All mappings, domains and reads checked before device creation.
        for (size_t q = 0; q < package->queries().size(); ++q)
            for (const auto& node : package->graph(q).nodes) {
                auto s = package->stage(node);
                profile->demand(input(s));
                if (!s.metadata.accesses.empty())
                    layout->resolve(s.metadata.accesses);
            }
        auto out = resolve("output");
        need(!std::filesystem::exists(out), "output must be new");
        trace.open(out);
        emit({{"type", "configuration"},
              {"config", c},
              {"context", "example-readonly/1"},
              {"layout_sha256", layout->source_sha256()},
              {"device_sha256", config->source_sha256()},
              {"profile_sha256", profile->source_sha256()}});
        device = std::make_unique<OrdinaryDevice>(8, *config, *layout);
        local = std::make_unique<timing::MetadataLocalReads>(
            *static_cast<SSD_Components::Host_Interface_NVMe*>(device->device->Host_interface),
            b,
            n);
        rules.add("example-sum/1", [](auto m) -> timing::MessageSize {
            using K = timing::MessageKind;
            switch (m.kind) {
            case K::Open:
                return {0, 32};
            case K::Execute:
                return {0, 48};
            case K::Result:
                return {8, 40};
            default:
                return {0, 32};
            }
        });
        pcie = std::make_unique<timing::MetadataPcie>(*device->link, b, n, rules);
        controller = std::make_unique<timing::MetadataController>(
            runtime,
            *local,
            *pcie,
            rules,
            b,
            n,
            timing::ControllerResources{2048, 8, 1024, 1, 16, 2});
        local->observe = [this](const auto& x) {
            local_count += x.requests.size();
            emit({{"type", "local_read"},
                  {"op", x.context.op},
                  {"logical_bytes", x.plan.logical_bytes},
                  {"sector_bytes", x.plan.sector_bytes}});
        };
        pcie->observations().sink = [this](const auto& x) {
            emit({{"type", "protocol"},
                  {"kind", int(x.kind)},
                  {"logical", x.logical},
                  {"payload", x.payload},
                  {"wire", x.wire},
                  {"arrived", x.arrived}});
        };
        controller->compute_observations().sink = [this](const auto& x) {
            emit({{"type", "controller_compute"},
                  {"duration_ns", x.duration_ns},
                  {"start", x.start},
                  {"end", x.end}});
        };
        device->root->data_arrived = [this](auto a, auto bytes) {
            if (launched)
                dma += bytes;
            device->flow->data(a, bytes);
        };
        dag::Config schedule;
        schedule.workers = 1;
        schedule.admission = 1;
        executor = std::make_unique<dag::Executor>(
            *package, runtime, b, n, schedule, [this](const auto& s) {
                return lower(s);
            });
        executor->stages().sink = [this](auto x) {
            emit({{"type", "stage"},
                  {"op", x.key.op},
                  {"query", x.key.query},
                  {"stage", x.key.stage},
                  {"submitted", x.submitted},
                  {"completed", x.completed},
                  {"success", x.success}});
        };
        executor->requests().sink = [this](const auto& x) {
            emit({{"type", "request"},
                  {"query", x.query},
                  {"offered", x.offered},
                  {"admitted", x.admitted},
                  {"visible", x.visible},
                  {"drained", x.drained},
                  {"success", x.success}});
        };
    }
    void start() {
        device->start();
        bridge.start();
    }
    bool finished() const {
        return finished_;
    }
    bool success() const {
        return ok;
    }
    void poll() {
        local->poll();
        if (!launched && device->window->advance(!Simulator->Peek_next_time(), now())) {
            launched = true;
            origin = now();
            device->flow->completed = [this](auto x) {
                need(bool(io_done), "orphan ordinary read");
                auto f = std::move(io_done);
                f(x);
            };

            auto launch = [this](timing::Outcome x) {
                need(!x.failure, "open failed");
                executor->start([this](auto result) {
                    ok = !result.failure;
                    finished_ = true;
                });
            };
            if (offload)
                await(controller->open(message(timing::MessageKind::Open)), launch);
            else
                launch({});
        }
        if (finished_ && bridge.idle() && !reported) {
            verify();
            reported = true;
            emit({{"type", "summary"},
                  {"success", ok},
                  {"origin", origin},
                  {"drained", now()},
                  {"elapsed_ns", now() - origin},
                  {"ordinary_requests", ordinary_count},
                  {"local_requests", local_count},
                  {"input_d2h_bytes", dma},
                  {"runtime_tasks", runtime.task_records()},
                  {"buffer_used", controller->buffer().used()},
                  {"sessions", controller->sessions()},
                  {"full_acceptance_passed", false},
                  {"configured_unvalidated", true}});
        }
    }
    void verify() const {
        need(finished_ && ok && executor->drained() && !runtime.task_records() && !runtime.pins() &&
                 !runtime.owners(),
             "runtime not drained");
        need(ordinary.empty() && !device->flow->pending() && !local->pending() &&
                 !pcie->pending() && !controller->sessions() && !controller->pending_jobs() &&
                 !controller->pending_protocol() && !controller->buffer().used() &&
                 !controller->buffer().waiting() && !controller->compute().pending() &&
                 !controller->port().pending() && !cpu.pending(),
             "resources not drained");
        need(!offload || dma == 0, "offload input reached Host");
    }
};
} // namespace block_sum
