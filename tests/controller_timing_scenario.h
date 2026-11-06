#pragma once
#include "timing/controller.h"
#include "backend/ordinary_device.h"
#include <cstdio>
namespace ndpsim::test::p12 {
using namespace ndpsim::timing;
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
struct Scenario {
    OrdinaryDevice device{512};
    EventBridge& bridge;
    EventBridge::Clock now;
    MessageRules rules;
    Runtime runtime;
    MetadataLocalReads reads;
    MetadataPcie pcie;
    MetadataController controller;
    bool launched = false, finished = false;
    unsigned completed = 0;
    uint64_t origin = 0, dma = 0;
    CompletionToken opened, job, closed, child, extra;
    std::string fault;
    bool injected = false;
    bool failing() const {
        return !fault.empty() && fault.find("delay-") != 0 && fault != "zero-compute" &&
               fault != "credit-wait";
    }
    std::vector<std::pair<std::string, uint64_t>> stages;
    Scenario(EventBridge& b, EventBridge::Clock n, std::string f = "")
        : bridge(b), now(n), runtime(b, n),
          reads(*static_cast<SSD_Components::Host_Interface_NVMe*>(device.device->Host_interface),
                b,
                n),
          pcie(*device.link, b, n, rules),
          controller(runtime, reads, pcie, rules, b, n, {2288, 128, 2160, 1, 16, 1}), fault(f) {
        device.root->data_arrived = [this](auto a, auto bytes) {
            if (launched)
                dma += bytes;
            device.flow->data(a, bytes);
        };
        rules.add("competitor/1", [](auto) {
            return MessageSize{0, 4096};
        });
        controller.observe = [this](const std::string& s, uint64_t) {
            stages.emplace_back(s, now());
            if (s == fault)
                controller.fail(job, "injected " + s);
            auto in_flight = [this] {
                injected = true;
                check(controller.buffer().used() > 128, "in-flight credit freed early");
                controller.fail(job, "in-flight fault");
                check(runtime.state(job) != State::Failed, "in-flight failed before drain");
            };
            if (((fault == "input-flight" || fault == "credit-failure") && s == "command") ||
                (fault == "compute-flight" && s == "compute-ready"))
                bridge.enqueue(now() + 1,
                               EventBridge::ContinueSubmit,
                               "test failure during actual service",
                               in_flight);
            if (fault == "return-flight" && s == "result")
                bridge.enqueue(now() + 7,
                               EventBridge::ContinueSubmit,
                               "test failure during return",
                               in_flight);
            if ((fault == "delay-input" && s == "reserved") ||
                (fault == "delay-return" && s == "result")) {
                auto m = message(MessageKind::Open);
                m.protocol = "competitor/1";
                if (fault == "delay-return") {
                    m.kind = MessageKind::OpenAck;
                    m.count = m.dimension = 0;
                    m.token = 1;
                }
                pcie.send(m, [](auto) {});
            }
        };
    }
    ControllerRequest request() {
        ControllerRequest r;
        r.command = message(MessageKind::Execute);
        r.result = message(MessageKind::Result);
        // Logical ranges map to two page-crossing requests, no real objects/files.
        r.reads.logical_bytes = 1080;
        r.reads.sector_bytes = 2048;
        r.reads.reads = {{7, 3, {}}, {32, 1, {}}};
        r.scratch_bytes = 76;
        r.operation.key = {0, "generic-compute", "arithmetic"};
        r.operation.duration_ns =
            fault == "delay-compute" ? 117 : (fault == "zero-compute" ? 0 : 17);
        r.operation.service_key = "fixture-constant/1:17ns";
        r.operation.work = {{"operations", {3, "count"}}};
        r.operation.outputs = {{"out", "opaque", 36, 1, ""}};
        return r;
    }
    CompletionToken profiled(ControllerRequest r) {
        cost::Registry registry;
        cost::register_builtin_formulas(registry);
        capture::StageSpec stage;
        stage.kind = "compute";
        stage.work_units = {{"operations", "count"}};
        stage.contexts = {"ssd-local/1"};
        stage.output_size_rule = "opaque/1";
        registry.add_operator({"fixture.kernel", "1", {"controller"}, {stage}, "fixture-oracle/1"});
        cost::Target target{"fixture-controller", "hypothetical", "none", "fixed", "one", 8192};
        auto raw = std::string(R"({"format":"ndpsim-cost-profile/1","fixture":true,"target":{
            "id":"fixture-controller","hardware":"hypothetical","os":"none","frequency":"fixed",
            "affinity":"one","memory_bytes":8192},"rules":[{"operator":"fixture.kernel",
            "operator_version":"1","stage_kind":"compute","context":"ssd-local/1",
            "location":"controller","target_id":"fixture-controller","work_units":{"operations":"count"},
            "feature_ranges":[{"name":"operations","unit":"count","minimum":0,"maximum":10}],
            "source":"prediction","formula":"constant/1","parameter_schema_sha256":")") +
                   registry.parameter_schema_sha256("constant/1") + R"(","parameters":{"ns":)" +
                   std::to_string(r.operation.duration_ns) + R"(},"measurement_sha256":[")" +
                   std::string(64, '1') + R"("],"training_query_ids":[1],"fit_report_sha256":")" +
                   std::string(64, '2') + R"("}]})";
        auto profile = cost::Profile::parse(raw, capture::sha256(raw), registry, target, true);
        check(profile.source_sha256() == capture::sha256(raw), "Profile provenance hash");
        cost::Input input;
        input.key = r.operation.key;
        input.operator_id = "fixture.kernel";
        input.operator_version = "1";
        input.stage_kind = "compute";
        input.context = "ssd-local/1";
        input.location = "controller";
        input.work = r.operation.work;
        input.target = target;
        auto bad = input;
        bad.location = "host";
        rejects([&] {
            controller.execute_profile(r, profile, bad);
        });
        bad = input;
        bad.work["operations"].first = 4;
        rejects([&] {
            controller.execute_profile(r, profile, bad);
        });
        bad = input;
        bad.context = "resident/1";
        rejects([&] {
            controller.execute_profile(r, profile, bad);
        });
        bad = input;
        bad.target.hardware = "other";
        rejects([&] {
            controller.execute_profile(r, profile, bad);
        });
        return controller.execute_profile(r, profile, input);
    }
    void start() {
        device.start();
    }
    void poll() {
        if (!launched && device.window->advance(!Simulator->Peek_next_time(), now())) {
            launched = true;
            origin = now();
            rejects([&] {
                MetadataController bad(
                    runtime, reads, pcie, rules, bridge, now, {4096, 128, 4096, 1, 16, 1});
            });
            opened = controller.open(message(MessageKind::Open));
            rejects([&] {
                controller.open(message(MessageKind::Open));
            });
            auto bad = request();
            bad.reads.reads.back().lba = UINT64_MAX;
            rejects([&] {
                controller.execute(bad);
            });
            check(!reads.pending() && !controller.pending_jobs(), "invalid plan partial injection");
            bad = request();
            bad.scratch_bytes = UINT64_MAX;
            rejects([&] {
                controller.execute(bad);
            });
            bad = request();
            bad.operation.outputs[0].logical_bytes = 35;
            rejects([&] {
                controller.execute(bad);
            });
            auto overlap = request().reads;
            overlap.logical_bytes = 4096;
            reads.validate(overlap); // Repeated logical accesses may share physical sectors.
            job = profiled(request());
            rejects([&] {
                controller.execute(request());
            });
            if (fault == "credit-wait" || fault == "credit-failure") {
                auto r = request();
                r.command.chunk = r.result.chunk = 2;
                r.operation.key.op = "second-block";
                extra = profiled(r);
                runtime.when_complete(extra, [this](Outcome x) {
                    check(bool(x.failure) == failing(), "queued block outcome");
                    ++completed;
                });
            }
            Operation o;
            o.key = {0, "successor", "test"};
            o.resource = new_resource();
            o.inputs = {job};
            child = runtime.submit(o);
            runtime.when_complete(child, [this](Outcome x) {
                check(bool(x.failure) == failing(), "successor failure gate");
                ++completed;
            });
            runtime.when_complete(job, [this](Outcome x) {
                check(bool(x.failure) == failing(), "job outcome");
                ++completed;
            });
            closed = controller.close(0);
            runtime.when_complete(closed, [this](Outcome x) {
                check(!x.failure, "close failed");
                ++completed;
            });
        }
        reads.poll();
        if (launched &&
            completed == ((fault == "credit-wait" || fault == "credit-failure") ? 4 : 3) &&
            !Simulator->Peek_next_time() && !device.flow->pending()) {
            check(!dma, "local vectors reached Host");
            check(!reads.pending() && !reads.pending_requests() && !pcie.pending() &&
                      !controller.sessions() && !controller.pending_jobs() &&
                      !controller.pending_protocol(),
                  "endpoint leak");
            check(!controller.buffer().used() && !controller.buffer().waiting() &&
                      !controller.buffer().live() && !controller.port().pending() &&
                      !controller.compute().pending(),
                  "resource leak");
            if (!failing() && fault != "credit-wait") {
                const auto& compute = controller.compute().records();
                check(compute.size() == 1 &&
                          compute[0].duration_ns ==
                              (fault == "delay-compute" ? 117 : (fault == "zero-compute" ? 0 : 17)),
                      "explicit demand");
                const auto& port = controller.port().records();
                check(port.size() == 5 && port[0].duration_ns == 8 && port[1].duration_ns == 128 &&
                          port[2].duration_ns == 136 && port[3].duration_ns == 3 &&
                          port[4].duration_ns == 3,
                      "independent buffer service math");
            }
            if (!failing() && fault != "credit-wait") {
                uint64_t expected = 154162;
                if (fault == "delay-compute")
                    expected += 100;
                if (fault == "zero-compute")
                    expected -= 17;
                // 4096 + 32*28 = 4992B / 4 lanes = 1248ns.
                if (fault == "delay-input")
                    expected += 1248;
                // Result write/read overlap first three ns of competitor serialization.
                if (fault == "delay-return")
                    expected += 1245;
                check(now() - origin == expected, "independent end-to-end timing/delay injection");
                auto result =
                    std::find_if(pcie.records().begin(), pcie.records().end(), [](const auto& r) {
                        return r.kind == MessageKind::Result;
                    });
                check(result != pcie.records().end() &&
                          successor.records()[0].start == result->arrived,
                      "successor before actual return");
            }
            rejects([&] {
                controller.open(message(MessageKind::Open));
            });
            runtime.release(opened);
            runtime.release(job);
            runtime.release(child);
            runtime.release(closed);
            if (extra.id())
                runtime.release(extra);
            finished = true;
        }
    }
    FifoResource* new_resource() {
        return &successor;
    }
    FifoResource successor{bridge, now, 1};
    void verify() {
        check(finished && !runtime.task_records() && !runtime.pins() && !runtime.owners(),
              "runtime ownership leak");
        if (fault == "credit-wait") {
            check(controller.compute().records().size() == 2 &&
                      controller.port().records().size() == 9,
                  "credit queue lost work");
            auto first =
                std::find_if(pcie.records().begin(), pcie.records().end(), [](const auto& r) {
                    return r.kind == MessageKind::Result;
                });
            check(controller.compute().records()[1].start >= first->arrived, "block credit bypass");
        }
        check(successor.records().size() == (failing() ? 0 : 1), "failed successor issued service");
        std::printf("P12 Controller PASS fault=%s origin=%llu end=%llu vector_d2h=%llu\n",
                    fault.c_str(),
                    origin,
                    now(),
                    dma);
    }
};

} // namespace ndpsim::test::p12
