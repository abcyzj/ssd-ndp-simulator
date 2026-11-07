// Consumer oracle: use the production C++ Profile loader for Python-generated data.
#include "model/cost/cost_model.h"
#include "serialization/json_schema.h"
#include <iostream>
using namespace ndpsim;
using Json = serialization::Json;
cost::Target target(const Json& t) {
    return {t.at("id"),
            t.at("hardware"),
            t.at("os"),
            t.at("frequency"),
            t.at("affinity"),
            t.at("memory_bytes")};
}
cost::Counters counters(const Json& c) {
    cost::Counters out;
    for (auto i = c.begin(); i != c.end(); ++i)
        out[i.key()] = {i.value().at("value"), i.value().at("unit")};
    return out;
}
int main() {
    std::string line;
    while (std::getline(std::cin, line)) {
        try {
            auto doc = Json::parse(line);
            cost::Registry registry;
            cost::register_builtin_formulas(registry);
            for (const auto& s : doc["operators"]) {
                capture::OperatorSpec op;
                op.id = s["id"];
                op.version = s["version"];
                op.locations = s["locations"].get<std::vector<std::string>>();
                op.semantic_validator = s["semantic_validator"];
                for (const auto& st : s["stages"]) {
                    capture::StageSpec spec;
                    spec.kind = st["kind"];
                    spec.contexts = st["contexts"].get<std::vector<std::string>>();
                    spec.output_size_rule = st["output_size_rule"];
                    spec.work_units = st["work_units"].get<std::map<std::string, std::string>>();
                    spec.feature_units =
                        st["feature_units"].get<std::map<std::string, std::string>>();
                    op.stages.push_back(spec);
                }
                registry.add_operator(op);
            }
            const auto raw = doc["profile"].dump();
            const auto profile = cost::Profile::parse(
                raw, capture::sha256(raw), registry, target(doc["target"]), true);
            Json output = Json::array();
            for (const auto& row : doc["inputs"]) {
                cost::Input input;
                input.key = {row["key"]["query"], row["key"]["op"], row["key"]["stage"]};
                input.operator_id = row["operator"];
                input.operator_version = row["operator_version"];
                input.stage_kind = row["stage_kind"];
                input.location = row["location"];
                input.context = row["context"];
                input.work = counters(row["work"]);
                input.features = counters(row["features"]);
                if (row.contains("metadata_features"))
                    input.metadata_features = counters(row["metadata_features"]);
                input.target = target(doc["target"]);
                if (row.contains("binding"))
                    input.binding_json = row["binding"].dump();
                output.push_back(profile.demand(input));
            }
            Json hashes = Json::object();
            for (const auto* id : {"constant/1",
                                   "nonnegative-linear/1",
                                   "upper-bound-table/1",
                                   "log-gbdt/1",
                                   "integer-rate/1"})
                hashes[id] = registry.parameter_schema_sha256(id);
            std::cout << Json({{"ns", output}, {"schema_sha256", hashes}}).dump() << std::endl;
        } catch (const std::exception& e) {
            std::cout << Json({{"error", e.what()}}).dump() << std::endl;
        }
    }
}
