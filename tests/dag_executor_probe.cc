#include "dag/executor.h"
#include <fstream>
#include <iostream>
#include <nlohmann/json.hpp>
#include <sys/resource.h>
using Json = nlohmann::json;
using namespace ndpsim;
int main(int argc, char **argv) {
    try {
        if (argc != 3)
            throw std::runtime_error("config output required");
        Json c;
        std::ifstream(argv[1]) >> c;
        dag::Receipt receipt;
        receipt.report = c.at("report").get<std::string>();
        receipt.report_sha256 = c.at("report_sha256");
        receipt.package_sha256 = c.at("package_sha256");
        receipt.registry_sha256 = c.at("registry_sha256");
        receipt.allow_fixture = c.value("allow_fixture", false);
        receipt.semantic_report = c.value("semantic_report", "");
        receipt.semantic_sha256 = c.value("semantic_sha256", "");
        dag::Limits limits;
        limits.scratch_bytes = c.value("scratch_bytes", limits.scratch_bytes);
        dag::Package package(c.at("package").get<std::string>(), receipt,
                             c.at("scratch").get<std::string>(), limits);
        uint64_t now = 0;
        std::multimap<uint64_t, uint64_t> wakes;
        EventBridge bridge([&] { return now; },
                           [&](auto delay, auto generation) {
                               wakes.emplace(timing::checked_add(now, delay), generation);
                           },
                           1000000, EventBridge::BackendMode::PlatformOnly);
        timing::Runtime runtime(bridge, [&] { return now; });
        timing::HostCpu cpu(bridge, [&] { return now; }, c.value("slots", 2));
        auto cfg = dag::Config{};
        cfg.admission = c.value("admission", 1);
        cfg.workers = c.value("workers", 1);
        cfg.max_active_stages = c.value("max_active_stages", cfg.max_active_stages);
        cfg.max_active_index_bytes = c.value("max_active_index_bytes", cfg.max_active_index_bytes);
        if (c.contains("offered_ns"))
            cfg.offered_ns = c.at("offered_ns").get<std::vector<uint64_t>>();
        auto adapter = [&](uint64_t delay, bool fail) {
            return [&, delay, fail](timing::Done done) {
                bridge.enqueue(
                    timing::checked_add(now, delay), EventBridge::Backend, "test adapter",
                    [done, fail] {
                        done({fail ? std::optional<timing::Failure>{{0, "Injected", "test fault"}}
                                   : std::nullopt});
                    });
            };
        };
        auto lower = [&](const dag::Stage &stage) {
            timing::Operation op;
            op.key = stage.key;
            op.work = stage.metadata.work;
            op.features = stage.metadata.features;
            op.outputs = stage.metadata.outputs;
            op.resource = &cpu;
            auto name = stage.key.op;
            auto duration = c.at("durations").at(name).get<uint64_t>();
            op.duration_ns = duration;
            uint64_t in = c.value("inputs", Json::object()).value(name, uint64_t(0));
            uint64_t ret = c.value("returns", Json::object()).value(name, uint64_t(0));
            bool fail = c.value("fail_op", "") == name;
            if (in)
                op.input = adapter(in, false);
            if (ret || fail)
                op.send_return = adapter(ret, fail);
            if (c.value("mutate_work", false))
                op.work.clear();
            return op;
        };
        dag::Executor executor(package, runtime, bridge, [&] { return now; }, cfg, lower);
        Json requests = Json::array(), stages = Json::array();
        executor.requests().sink = [&](const dag::RequestRecord &r) {
            requests.push_back({{"query", r.query},
                                {"offered", r.offered},
                                {"admitted", r.admitted},
                                {"worker_start", r.worker_start},
                                {"visible", r.visible},
                                {"drained", r.drained},
                                {"success", r.success}});
        };
        executor.stages().sink = [&](const dag::StageRecord &s) {
            stages.push_back({{"query", s.key.query},
                              {"op", s.key.op},
                              {"stage", s.key.stage},
                              {"submitted", s.submitted},
                              {"completed", s.completed},
                              {"success", s.success}});
        };
        if (c.value("statistics_only", false)) {
            executor.requests().sink = {};
            executor.requests().enabled = false;
            executor.stages().sink = {};
            executor.stages().enabled = false;
        }
        runtime.observations().enabled = false;
        cpu.observations().enabled = false;
        bool done = false;
        timing::Outcome outcome;
        uint64_t drained = 0;
        bridge.start();
        executor.start([&](timing::Outcome o) {
            done = true;
            outcome = o;
            drained = now;
        });
        if (c.value("duplicate_start", false))
            executor.start([](auto) {});
        while (!wakes.empty()) {
            auto wake = *wakes.begin();
            wakes.erase(wakes.begin());
            now = wake.first;
            bridge.wake(wake.second);
            executor.require_progress();
        }
        if (!done || !executor.drained())
            throw std::runtime_error("no final drain");
        const auto &m = executor.metrics();
        Json result = {{"success", !outcome.failure},
                       {"drained", drained},
                       {"requests", requests},
                       {"stages", stages},
                       {"admitted", m.admitted},
                       {"completed", m.completed},
                       {"skipped", m.skipped},
                       {"peak_queries", m.peak_queries},
                       {"peak_workers", m.peak_workers},
                       {"peak_stages", m.peak_stages},
                       {"peak_index_bytes", m.peak_index_bytes},
                       {"active_queries", m.active_queries},
                       {"active_workers", m.active_workers},
                       {"active_stages", m.active_stages},
                       {"active_index_bytes", m.active_index_bytes},
                       {"outstanding", m.outstanding},
                       {"ready_edges", m.ready_edges},
                       {"runtime_tasks", runtime.task_records()},
                       {"runtime_pins", runtime.pins()},
                       {"services", cpu.observations().count},
                       {"resource_pending", cpu.pending()},
                       {"detail_reads", package.detail_reads()},
                       {"peak_detail_bytes", package.peak_detail_bytes()},
                       {"scratch_bytes", package.scratch_bytes()}};
        struct rusage usage{};
        if (getrusage(RUSAGE_SELF, &usage) == 0) {
#ifdef __APPLE__
            result["rss_bytes"] = usage.ru_maxrss;
#else
            result["rss_bytes"] = uint64_t(usage.ru_maxrss) * 1024;
#endif
        }
        if (outcome.failure)
            result["failure"] = {{"source", outcome.failure->source},
                                 {"code", outcome.failure->code},
                                 {"detail", outcome.failure->detail}};
        std::ofstream(argv[2]) << result.dump(2) << '\n';
        return outcome.failure ? 3 : 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 2;
    }
}
