#include "model/host_cpu.h"
#include <fstream>
#include <iostream>
#include <nlohmann/json.hpp>

using namespace ndpsim;
using namespace ndpsim::model;
using nlohmann::json;

void check(bool ok, const char* detail) {
    if (!ok)
        throw std::runtime_error(detail);
}
template <class F> void rejects(const char* label, F action) {
    try {
        action();
    } catch (const content::Error& e) {
        std::cout << label << ": " << e.what() << '\n';
        return;
    }
    throw std::runtime_error(std::string("accepted ") + label);
}

int main(int argc, char** argv) {
    check(argc == 3, "expected fixture and scratch path");
    const CpuApplicability actual{
        "fixture",
        "fixture",
        64,
        "Unsupported",
        "Unsupported",
        "2dc815a9cf7b6bf38c7bc32d97feec1ebfbba6745fba363ff4058e4db6c5a22b",
        "74dd6752f5b83360c8fb780f06d74e5edb2712ea55a59b02a263584b091993a4",
        "synthetic-fixture",
        {"-fno-fast-math", "-ffp-contract=off"},
        1,
        2,
        "resident_random",
        1};
    std::ifstream input(argv[1]);
    auto fixture = json::parse(input);
    auto load_raw =
        [&](const std::string& raw, const CpuApplicability& binding, bool allow = true) {
            std::ofstream out(argv[2], std::ios::binary);
            out << raw;
            out.close();
            return CpuProfile::load(
                argv[2],
                content::sha256(reinterpret_cast<const uint8_t*>(raw.data()), raw.size()),
                binding,
                allow);
        };
    auto load = [&](const json& doc) {
        return load_raw(doc.dump(), actual);
    };
    auto frozen_fixture = load(fixture);
    operators::Work work;
    work.flops = 6;
    check(frozen_fixture.estimate("SCORE_L2", "score_l2", work, {{"vectors", 1}}) == 4,
          "frozen fixture constant");

    fixture["models"][0]["model"]["ns"] = 2.25;
    fixture["models"][1]["features"] =
        json::array({{{"name", "vectors"}, {"unit", "count"}, {"minimum", 1}, {"maximum", 4}},
                     {{"name", "flops"}, {"unit", "flop"}, {"minimum", 0}, {"maximum", 24}}});
    fixture["models"][1]["model"] = {
        {"kind", "linear"}, {"intercept_ns", 0.25}, {"coefficients", {0.5, 0.25}}};
    auto table = fixture["models"][0];
    table["operator"] = "FINISH";
    table["stage"] = "finish";
    table["features"][0] = {
        {"name", "elements"}, {"unit", "count"}, {"minimum", 0}, {"maximum", 5}};
    table["model"] = {{"kind", "table"},
                      {"bins",
                       {{{"upper", 1}, {"ns", 0}},
                        {{"upper", 3}, {"ns", 7.125}},
                        {{"upper", 5}, {"ns", UINT64_MAX}}}}};
    fixture["models"].push_back(table);
    auto profile = load(fixture);
    std::ofstream(std::string(argv[2]) + ".valid.json") << fixture.dump(2) << '\n';
    check(profile.estimate("SCORE_L2", "score_l2", operators::Work{}, {{"vectors", 1}}) == 1,
          "linear lower bound");
    auto upper_work = work;
    upper_work.flops = 24;
    check(profile.estimate("SCORE_L2", "score_l2", upper_work, {{"vectors", 4}}) == 9,
          "linear upper bound");
    check(profile.estimate("SCORE_L2", "score_prepare", work, {{"vectors", 1}}) == 3,
          "ceil constant");
    for (int repeat = 0; repeat < 2; ++repeat)
        check(profile.estimate("SCORE_L2", "score_l2", work, {{"vectors", 2}}) == 3,
              "ceil ordered linear");
    const std::vector<uint64_t> expected{0, 0, 8, 8, UINT64_MAX, UINT64_MAX};
    for (work.elements = 0; work.elements < expected.size(); ++work.elements)
        check(profile.estimate("FINISH", "finish", work) == expected[work.elements],
              "table boundary");
    std::cout << "constant ceil(2.25)=3; linear ceil(0.25+2*0.5+6*0.25)=3; "
                 "table [0,1]=0 [2,3]=8 [4,5]=UINT64_MAX; same work switched 4->3\n";

    rejects("unknown operator", [&] {
        profile.estimate("UNKNOWN", "score_l2", work);
    });
    rejects("invalid stage", [&] {
        profile.estimate("SCORE_L2", "finish", work);
    });
    rejects("missing model", [&] {
        profile.estimate("INIT", "init", work);
    });
    rejects("missing feature", [&] {
        profile.estimate("SCORE_L2", "score_l2", work);
    });
    rejects("above table range", [&] {
        profile.estimate("FINISH", "finish", work);
    });
    rejects("below constant range", [&] {
        profile.estimate("SCORE_L2", "score_prepare", work, {{"vectors", 0}});
    });
    rejects("Work override", [&] {
        profile.estimate("SCORE_L2", "score_l2", work, {{"vectors", 2}, {"flops", 1}});
    });
    rejects("profile hash", [&] {
        CpuProfile::load(argv[2], std::string(64, '0'), actual);
    });
    rejects("missing file", [&] {
        CpuProfile::load(std::string(argv[2]) + ".absent", "", actual);
    });
    rejects("fixture scope", [&] {
        load_raw(fixture.dump(), actual, false);
    });
    for (const auto& field : {"build",
                              "hardware",
                              "os",
                              "preparation_context",
                              "workers",
                              "dimensions",
                              "records",
                              "memory_bytes",
                              "frequency",
                              "affinity"}) {
        auto binding = actual;
        auto bad = fixture;
        auto& v = bad["applicability"][field];
        if (v.is_string())
            v = std::string(field) == "preparation_context" ? "ssd_return_buffer" : "different";
        else if (v.is_object())
            v["compiler"] = "different";
        else
            v = 2;
        if (std::string(field) == "records")
            v = 3;
        rejects(field, [&] {
            load_raw(bad.dump(), binding);
        });
    }
    auto changed = [&](const char* label, auto mutate) {
        auto bad = fixture;
        mutate(bad);
        rejects(label, [&] {
            load(bad);
        });
    };
    for (const auto* version : {"ndpsim-cpu-profile/1", "ndpsim-cpu-profile/2"})
        changed("retired version", [version](auto& d) { d["format"] = version; });
    auto boosted = fixture;
    auto& dedup = boosted["models"][2];
    dedup["features"] = table["features"];
    dedup["model"] = {{"kind", "log_gbdt"}, {"initial", 0.}, {"rate", 1.},
                      {"trees", json::array({json::array({
                          {{"feature", 0}, {"threshold", 2}, {"right", 2}}, 1., 2.})})}};
    auto boosted_profile = load(boosted);
    work.elements = 2;
    check(boosted_profile.estimate("DEDUP_NEIGHBORS", "dedup", work) == 3, "GBDT left");
    work.elements = 3;
    check(boosted_profile.estimate("DEDUP_NEIGHBORS", "dedup", work) == 8, "GBDT right");
    work.elements = 6;
    rejects("GBDT out of range", [&] {
        boosted_profile.estimate("DEDUP_NEIGHBORS", "dedup", work);
    });
    boosted["models"][2]["model"]["trees"][0][0]["right"] = 3;
    rejects("GBDT bad child", [&] { load(boosted); });
    changed("dedup without GBDT", [](auto& d) {
        d["models"][2]["model"] = {{"kind", "constant"}, {"ns", 1}};
    });

    auto split = fixture;
    for (size_t i : {3, 4}) {
        auto& phase = split["models"][i];
        phase["features"] = fixture["models"][1]["features"];
        phase["model"] = fixture["models"][1]["model"];
        phase["model"]["intercept_ns"] = i == 3 ? 20 : 40;
    }
    auto split_profile = load(split);
    check(split_profile.estimate("MERGE_CANDIDATES", "merge_append", {}, {{"vectors", 2}}) == 21,
          "split append");
    check(split_profile.estimate("MERGE_CANDIDATES", "merge_finalize", {}, {{"vectors", 2}}) == 41,
          "split finalize");
    rejects("split append out of range", [&] {
        split_profile.estimate("MERGE_CANDIDATES", "merge_append", {}, {{"vectors", 5}});
    });
    rejects("retired mixed stage", [&] {
        split_profile.estimate("MERGE_CANDIDATES", "merge", {}, {{"vectors", 2}});
    });
    changed("missing finalize stage", [](auto& d) { d["models"].erase(4); });
    changed("rounding", [](auto& d) {
        d["rounding"] = "floor";
    });
    changed("negative", [](auto& d) {
        d["models"][0]["model"]["ns"] = -1;
    });
    changed("negative coefficient", [](auto& d) {
        d["models"][1]["model"]["coefficients"][0] = -0.01;
    });
    changed("overflow", [](auto& d) {
        d["models"][1]["model"]["coefficients"][0] = 1e308;
    });
    changed("2^64", [](auto& d) {
        d["models"][0]["model"]["ns"] = 0x1p64;
    });
    changed("units", [](auto& d) {
        d["models"][1]["features"][1]["unit"] = "B";
    });
    changed("duplicate model", [](auto& d) {
        auto row = d["models"][0];
        d["models"].push_back(row);
    });
    changed("table gap", [](auto& d) {
        d["models"].back()["model"]["bins"].erase(2);
    });
    changed("coefficient shape", [](auto& d) {
        d["models"][1]["model"]["coefficients"].erase(0);
    });
    changed("missing provenance", [](auto& d) {
        d["training"].erase("measurement_sha256");
    });
    auto raw = fixture.dump();
    raw.replace(raw.find("2.25"), 4, "NaN");
    rejects("NaN", [&] {
        load_raw(raw, actual);
    });
    rejects("duplicate JSON key", [&] {
        load_raw("{\"format\":0," + fixture.dump().substr(1), actual);
    });

    uint64_t now = 0;
    std::multimap<uint64_t, uint64_t> wakes;
    EventBridge bridge(
        [&] {
            return now;
        },
        [&](auto d, auto generation) {
            wakes.emplace(now + d, generation);
        });
    runtime::Runtime runtime(bridge, [&] {
        return now;
    });
    Simulator->Reset();
    Simulator->Initialize_simulation();
    bridge.start();
    HostConfig cfg;
    cfg.distance = {42, 0};
    cfg.cpu_profile = std::make_shared<CpuProfile>(profile);
    HostCpu cpu(
        runtime,
        bridge,
        [&] {
            return now;
        },
        cfg);
    auto evaluate = [](const auto&) {
        operators::Result result;
        result.work.flops = 6;
        return result;
    };
    rejects("explicit profile without stage", [&] {
        cpu.submit(1, {}, Kind::Distance, evaluate);
    });
    rejects("legacy API with explicit profile", [&] {
        service_ns(work, Kind::Distance, cfg);
    });
    auto good_stage = CpuStage{"SCORE_L2", "score_l2", [](const auto&, const auto&) {
                                   return CpuFeatures{{"vectors", 2}};
                               }};
    auto bad = cpu.submit(1, {}, Kind::Distance, evaluate, CpuStage{"INIT", "init", {}});
    auto successor = cpu.submit(1, {bad}, Kind::Distance, evaluate, good_stage);
    auto good = cpu.submit(2, {}, Kind::Distance, evaluate, good_stage);
    auto zero = cpu.submit(3, {}, Kind::Control, evaluate, CpuStage{"FINISH", "finish", {}});
    while (!wakes.empty()) {
        auto wake = *wakes.begin();
        wakes.erase(wakes.begin());
        now = wake.first;
        bridge.wake(wake.second);
        runtime.require_progress();
    }
    check(runtime.state(bad.id()) == runtime::State::Failed &&
              runtime.state(successor.id()) == runtime::State::Failed,
          "failure gates dependent");
    check(runtime.state(good.id()) == runtime::State::Visible &&
              runtime.state(zero.id()) == runtime::State::Visible,
          "successful services publish");
    check(cpu.busy() == 0 && cpu.services().size() == 2 && now == 3 &&
              cpu.services()[0].service_ns == 3 && cpu.services()[1].service_ns == 0,
          "no fallback, no failed service charge, slot drained");
    for (auto handle : {bad, successor, good, zero})
        runtime.release(handle);
    std::cout
        << "HostCpu: failure and dependent gated, independent services 3/0 ns, slots drained\n";
}
