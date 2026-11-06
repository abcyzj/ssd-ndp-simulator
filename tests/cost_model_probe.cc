#include "model/cost/cost_model.h"
#include "serialization/json_schema.h"
#include <iostream>
using namespace ndpsim;
using Json = serialization::Json;
using namespace cost;
Target target(const Json& j) {
    return {j.at("id"),
            j.at("hardware"),
            j.at("os"),
            j.at("frequency"),
            j.at("affinity"),
            j.at("memory_bytes")};
}
Counters counters(const Json& j) {
    Counters c;
    for (auto i = j.begin(); i != j.end(); ++i)
        c[i.key()] = {i.value().at("value"), i.value().at("unit")};
    return c;
}
int main() {
    Registry registry;
    register_builtin_formulas(registry);
    capture::StageSpec stage;
    stage.kind = "compute";
    stage.work_units = {{"flops", "flop"}, {"bytes", "B"}};
    stage.feature_units = {{"items", "count"}};
    stage.contexts = {"resident/1", "ssd/1"};
    stage.output_size_rule = "example-output/1";
    capture::OperatorSpec op{
        "example.kernel", "1", {"host", "controller"}, {stage}, "example-oracle/1"};
    registry.add_operator(op);
    // External formula uses only the public API. No changes to production library.
    registry.add_formula(
        "example-double/1",
        R"({"type":"object","properties":{},"additionalProperties":false,"description":"扩展"})",
        [](const auto&, const auto& ranges) -> Estimate {
            if (ranges.size() != 1 || ranges[0].maximum > UINT64_MAX / 2)
                throw Error("extension domain");
            return [](const auto& v) {
                return v[0] * 2;
            };
        });
    Json hashes = Json::object();
    for (const auto* id : {"constant/1",
                           "nonnegative-linear/1",
                           "upper-bound-table/1",
                           "log-gbdt/1",
                           "integer-rate/1",
                           "example-double/1"})
        hashes[id] = registry.parameter_schema_sha256(id);
    std::cout << hashes.dump() << std::endl;
    std::string line;
    while (std::getline(std::cin, line)) {
        try {
            auto req = Json::parse(line);
            if (req.value("duplicate_operator", false))
                registry.add_operator(op);
            if (req.value("duplicate_formula", false))
                register_builtin_formulas(registry);
            if (req.value("bad_schema", false))
                registry.add_formula("bad/1",
                                     R"({"type":"number","multipleOf":2})",
                                     [](const auto&, const auto&) -> Estimate {
                                         return {};
                                     });
            const auto raw =
                req.contains("raw") ? req["raw"].get<std::string>() : req.at("profile").dump();
            auto profile = Profile::parse(raw,
                                          req.value("expected_sha256", capture::sha256(raw)),
                                          registry,
                                          target(req["target"]),
                                          req.value("allow_fixture", false));
            const auto& j = req["input"];
            Input input{{j["key"]["query"], j["key"]["op"], j["key"]["stage"]},
                        j["operator"],
                        j["operator_version"],
                        j["stage_kind"],
                        j["context"],
                        j["location"],
                        counters(j["work"]),
                        counters(j["features"]),
                        target(j["target"]),
                        j.contains("binding") ? j["binding"].dump() : ""};
            std::cout << Json({{"ns", profile.demand(input)}}).dump() << std::endl;
        } catch (const std::exception& e) {
            std::cout << Json({{"error", e.what()}}).dump() << std::endl;
        }
    }
}
