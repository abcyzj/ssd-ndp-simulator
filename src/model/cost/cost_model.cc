#include "model/cost/cost_model.h"
#include "model/cost/schemas.h"
#include "serialization/json_schema.h"
#include <tuple>
#include <set>
#include <cmath>
#include <limits>

namespace ndpsim::cost {
namespace {
using Json = serialization::Json;
void need(bool ok, const std::string& message) {
    if (!ok)
        throw Error("cost: " + message);
}
Json parse(const std::string& text) {
    need(text.size() <= 64 * 1024 * 1024, "JSON byte limit");
    unsigned nesting = 0;
    bool quoted = false, escaped = false;
    for (char c : text) {
        if (quoted) {
            if (escaped)
                escaped = false;
            else if (c == '\\')
                escaped = true;
            else if (c == '"')
                quoted = false;
        } else if (c == '"')
            quoted = true;
        else if (c == '{' || c == '[')
            need(++nesting <= 64, "JSON nesting limit");
        else if (c == '}' || c == ']') {
            need(nesting > 0, "JSON nesting mismatch");
            --nesting;
        }
    }
    try {
        return serialization::parse_unique(text, [](const std::string& k) {
            throw Error("duplicate key: " + k);
        });
    } catch (const Json::exception& e) {
        throw Error(e.what());
    }
}
const Json& contract() {
    static const auto j = parse(embedded::contract);
    return j;
}
// Deliberately small Draft2020-12 subset used by the frozen schemas and formulas.
// Reject unsupported validation keywords for extension schemas at registration.
void supported(const Json& s) {
    static const std::set<std::string> keys{"$schema",     "$id",         "$defs",
                                            "title",       "description", "type",
                                            "properties",  "required",    "additionalProperties",
                                            "items",       "minItems",    "maxItems",
                                            "uniqueItems", "minimum",     "maximum",
                                            "minLength",   "maxLength",   "pattern",
                                            "enum",        "const",       "oneOf"};
    need(s.is_object(), "schema must be object");
    for (auto it = s.begin(); it != s.end(); ++it) {
        need(keys.count(it.key()), "unsupported schema keyword: " + it.key());
        if (it.key() == "properties" || it.key() == "$defs")
            for (const auto& v : it.value())
                supported(v);
        if (it.key() == "items" || (it.key() == "additionalProperties" && it->is_object()))
            supported(*it);
        if (it.key() == "oneOf")
            for (const auto& v : *it)
                supported(v);
    }
}
void validate(const Json& v, const Json& s, const Json& root) {
    if (s.contains("$ref")) {
        const auto ref = s.at("$ref").get<std::string>();
        if (ref.rfind("urn:ndpsim:contract:2#", 0) == 0)
            return validate(v, contract().at(Json::json_pointer(ref.substr(22))), contract());
        need(ref.rfind("#/", 0) == 0, "unsupported schema ref");
        return validate(v, root.at(Json::json_pointer(ref.substr(1))), root);
    }
    // Reuse scalar/array checks, recursively handle cross-schema references and
    // dictionary values (not supported by the historical schema helper).
    if (s.contains("oneOf")) {
        unsigned count = 0;
        for (const auto& branch : s["oneOf"]) {
            try {
                validate(v, branch, root);
                ++count;
            } catch (const Error&) {
            }
        }
        need(count == 1, "expected one schema alternative");
        Json siblings = s;
        siblings.erase("oneOf");
        return validate(v, siblings, root);
    }
    if (s.value("type", "") == "number" && s.contains("maximum") && v.is_number())
        need(v.get<double>() <= s["maximum"].get<double>(), "number above maximum");
    Json shallow = s;
    if (s.value("type", "") == "object") {
        need(v.is_object(), "expected object");
        for (const auto& k : s.value("required", Json::array()))
            need(v.contains(k.get<std::string>()), "missing field: " + k.get<std::string>());
        const auto props = s.value("properties", Json::object());
        for (auto i = v.begin(); i != v.end(); ++i) {
            if (props.contains(i.key()))
                validate(*i, props[i.key()], root);
            else {
                const auto additional = s.value("additionalProperties", Json(true));
                if (additional.is_object())
                    validate(*i, additional, root);
                else
                    need(additional == true, "unknown field: " + i.key());
            }
        }
        shallow.erase("properties");
        shallow.erase("required");
        shallow.erase("additionalProperties");
    } else if (s.value("type", "") == "array") {
        need(v.is_array(), "expected array");
        if (s.contains("items"))
            for (const auto& item : v)
                validate(item, s["items"], root);
        shallow["items"] = Json::object();
    }
    serialization::validate<Error>(
        v,
        shallow,
        "",
        root,
        [](bool ok, const std::string&, const std::string& msg, const std::string& = "") {
            need(ok, msg);
        });
}
Json target_json(const Target& t) {
    Json j{{"id", t.id},
           {"hardware", t.hardware},
           {"os", t.os},
           {"frequency", t.frequency},
           {"affinity", t.affinity},
           {"memory_bytes", t.memory_bytes}};
    validate(j, contract()["$defs"]["target"], contract());
    return j;
}
Ns rounded(double n) {
    need(std::isfinite(n) && n >= 0 && std::ceil(n) < 0x1p64,
         "service overflow/nonfinite/negative");
    return static_cast<Ns>(std::ceil(n));
}
std::string canonical(const Json& j) {
    return j.dump(-1, ' ', true) + "\n";
}
std::string hash(const Json& j) {
    return capture::sha256(canonical(j));
}
using RuleKey = std::tuple<std::string, std::string, std::string, std::string, std::string>;
using CallKey = std::tuple<std::uint64_t, std::string, std::string>;
RuleKey rule_key(const Json& r) {
    return {r["operator"], r["operator_version"], r["stage_kind"], r["context"], r["location"]};
}
CallKey call_key(const Json& k) {
    return {k["query"], k["op"], k["stage"]};
}
Json counters_json(const Counters& c) {
    Json j = Json::object();
    for (const auto& [name, q] : c)
        j[name] = {{"value", q.first}, {"unit", q.second}};
    return j;
}
void check_counters(const Json& values, const Json& units) {
    validate(values, contract()["$defs"]["counters"], contract());
    need(values.size() == units.size(), "counter set mismatch");
    for (auto i = units.begin(); i != units.end(); ++i)
        need(values.contains(i.key()) && values[i.key()]["unit"] == *i,
             "counter unit mismatch: " + i.key());
}
Json units(const std::map<std::string, std::string>& u) {
    return Json(u);
}
std::vector<Ns> values(const Json& work, const Json& features, const std::vector<Range>& ranges) {
    std::vector<Ns> out;
    for (const auto& r : ranges) {
        need(!(work.contains(r.name) && features.contains(r.name)), "ambiguous feature");
        const auto& c = work.contains(r.name) ? work : features;
        need(c.contains(r.name), "missing model feature: " + r.name);
        const auto& q = c[r.name];
        const auto n = q["value"].get<Ns>();
        need(q["unit"] == r.unit && n >= r.minimum && n <= r.maximum,
             "feature unit/range: " + r.name);
        out.push_back(n);
    }
    return out;
}
} // namespace
struct Registry::Data {
    struct Formula {
        Json schema;
        std::string hash;
        Compile compile;
    };
    std::map<std::string, Formula> formulas;
    std::map<std::pair<std::string, std::string>, capture::OperatorSpec> operators;
};
Registry::Registry() : data_(std::make_shared<Data>()) {
}
Registry::~Registry() = default;
void Registry::add_formula(std::string id, std::string schema_json, Compile compile) {
    need(!id.empty() && bool(compile), "formula ID/compiler required");
    auto schema = parse(schema_json);
    supported(schema);
    need(!data_->formulas.count(id), "duplicate formula: " + id);
    data_->formulas.emplace(id, Data::Formula{schema, hash(schema), std::move(compile)});
}
std::string Registry::parameter_schema_sha256(const std::string& id) const {
    const auto it = data_->formulas.find(id);
    need(it != data_->formulas.end(), "unknown formula: " + id);
    return it->second.hash;
}
void Registry::add_operator(capture::OperatorSpec op) {
    need(!op.id.empty() && !op.version.empty() && !op.stages.empty() && !op.locations.empty(),
         "incomplete operator");
    std::set<std::string> locations, stages;
    for (const auto& l : op.locations)
        need((l == "host" || l == "controller") && locations.insert(l).second,
             "unsupported/duplicate location");
    for (const auto& s : op.stages) {
        need(!s.kind.empty() && stages.insert(s.kind).second && !s.contexts.empty() &&
                 !s.output_size_rule.empty(),
             "incomplete/duplicate stage");
        std::set<std::string> contexts;
        for (const auto& c : s.contexts)
            need(!c.empty() && contexts.insert(c).second, "duplicate/empty context");
        for (const auto* u : {&s.work_units, &s.feature_units})
            for (const auto& [n, v] : *u)
                need(!n.empty() && !v.empty(), "empty feature/unit");
        for (const auto& [n, v] : s.work_units)
            need(!s.feature_units.count(n), "ambiguous Work/feature");
    }
    auto key = std::make_pair(op.id, op.version);
    need(data_->operators.emplace(key, std::move(op)).second, "duplicate operator");
}
struct Profile::Data {
    struct Rule {
        capture::StageSpec spec;
        std::vector<Range> ranges;
        Estimate estimate;
        Json binding;
        struct Row {
            Json work, features;
            Ns ns;
        };
        std::map<CallKey, Row> rows;
    };
    Json target;
    std::string source_sha256;
    std::map<RuleKey, Rule> rules;
};
const std::string& Profile::source_sha256() const { return data_->source_sha256; }
Profile Profile::parse(const std::string& raw,
                       const std::string& expected,
                       const Registry& registry,
                       const Target& target,
                       bool allow_fixture) {
    need(expected == capture::sha256(raw), "Profile hash mismatch");
    const auto doc = cost::parse(raw);
    const auto schema = cost::parse(embedded::profile);
    validate(doc, schema, schema);
    need(!doc["fixture"].get<bool>() || allow_fixture, "fixture requires explicit permission");
    if (!doc["fixture"].get<bool>()) {
        std::function<void(const Json&)> check_hashes = [&](const Json& value) {
            if (value.is_object())
                for (auto i = value.begin(); i != value.end(); ++i) {
                    if (i.key().find("sha256") != std::string::npos) {
                        if (i->is_string())
                            need(*i != std::string(64, '0'), "placeholder production identity");
                        if (i->is_array())
                            for (const auto& h : *i)
                                need(h != std::string(64, '0'), "placeholder production identity");
                    }
                    check_hashes(*i);
                }
            else if (value.is_array())
                for (const auto& item : value)
                    check_hashes(item);
        };
        check_hashes(doc);
    }
    auto data = std::make_shared<Data>();
    data->source_sha256 = expected;
    data->target = target_json(target);
    need(doc["target"] == data->target, "target hardware identity mismatch");
    for (const auto& row : doc["rules"]) {
        const auto op = registry.data_->operators.find({row["operator"], row["operator_version"]});
        need(op != registry.data_->operators.end(), "unknown operator/version");
        need(std::find(op->second.locations.begin(), op->second.locations.end(), row["location"]) !=
                 op->second.locations.end(),
             "unsupported operator location");
        const auto spec =
            std::find_if(op->second.stages.begin(), op->second.stages.end(), [&](const auto& s) {
                return s.kind == row["stage_kind"];
            });
        need(spec != op->second.stages.end(), "unknown stage");
        need(std::find(spec->contexts.begin(), spec->contexts.end(), row["context"]) !=
                 spec->contexts.end(),
             "unsupported context");
        need(row["target_id"] == target.id && row["work_units"] == units(spec->work_units),
             "target/Work spec mismatch");
        Data::Rule r;
        r.spec = *spec;
        std::set<std::string> names;
        for (const auto& f : row["feature_ranges"]) {
            Range range{f["name"], f["unit"], f["minimum"], f["maximum"]};
            need(names.insert(range.name).second && range.minimum <= range.maximum,
                 "duplicate/inverted feature range");
            auto all = spec->work_units;
            all.insert(spec->feature_units.begin(), spec->feature_units.end());
            need(all.count(range.name) && all.at(range.name) == range.unit,
                 "unregistered feature/unit");
            r.ranges.push_back(std::move(range));
        }
        // Every captured extra feature must have an applicability range, even if
        // its coefficient is zero. Work-only counters may be unmodelled.
        for (const auto& [n, u] : spec->feature_units)
            need(names.count(n), "missing extra feature range");
        const auto& measurements = row["measurement_sha256"];
        need(std::set<Json>(measurements.begin(), measurements.end()).size() == measurements.size(),
             "duplicate measurement artifact");
        if (row["source"] == "prediction") {
            need(std::set<Json>(row["training_query_ids"].begin(), row["training_query_ids"].end())
                         .size() == row["training_query_ids"].size(),
                 "duplicate training query ID");
            auto f = registry.data_->formulas.find(row["formula"]);
            need(f != registry.data_->formulas.end(), "unknown formula/version");
            need(row["parameter_schema_sha256"] == f->second.hash,
                 "parameter schema identity mismatch");
            validate(row["parameters"], f->second.schema, f->second.schema);
            r.estimate = f->second.compile(canonical(row["parameters"]), r.ranges);
            need(bool(r.estimate), "empty compiled model");
        } else {
            r.binding = row["binding"];
            need(doc["fixture"].get<bool>() || r.binding["provenance"]["kind"] == "native-capture",
                 "synthetic binding requires fixture Profile");
            need(r.binding["target"] == data->target && r.binding["context"] == row["context"],
                 "table target/context mismatch");
            for (const auto& item : row["rows"]) {
                check_counters(item["work"], units(spec->work_units));
                check_counters(item["features"], units(spec->feature_units));
                (void)values(item["work"], item["features"], r.ranges);
                const auto& ids = r.binding["provenance"]["query_ids"];
                need(std::find(ids.begin(), ids.end(), item["key"]["query"]) != ids.end(),
                     "table query outside binding");
                need(r.rows
                         .emplace(
                             call_key(item["key"]),
                             Data::Rule::Row{item["work"], item["features"], item["duration_ns"]})
                         .second,
                     "duplicate table call");
            }
        }
        need(data->rules.emplace(rule_key(row), std::move(r)).second, "duplicate rule");
    }
    return Profile(std::move(data));
}
Ns Profile::demand(const Input& input) const {
    need(target_json(input.target) == data_->target, "target hardware identity mismatch");
    const auto it = data_->rules.find({input.operator_id,
                                       input.operator_version,
                                       input.stage_kind,
                                       input.context,
                                       input.location});
    need(it != data_->rules.end(), "missing model for stage/context/location");
    const auto& r = it->second;
    const auto work = counters_json(input.work), features = counters_json(input.features);
    check_counters(work, units(r.spec.work_units));
    check_counters(features, units(r.spec.feature_units));
    const auto v = values(work, features, r.ranges);
    if (r.estimate)
        return r.estimate(v);
    need(cost::parse(input.binding_json) == r.binding, "measured table binding mismatch");
    const auto call = r.rows.find({input.key.query, input.key.op, input.key.stage});
    need(call != r.rows.end(), "missing measured call");
    need(call->second.work == work && call->second.features == features,
         "measured Work/features mismatch");
    return call->second.ns;
}
namespace {
Json object_schema(Json properties, Json required) {
    return {{"type", "object"},
            {"properties", std::move(properties)},
            {"required", std::move(required)},
            {"additionalProperties", false}};
}
Json array_schema(Json item, unsigned min = 0, unsigned max = 1000000) {
    return {{"type", "array"}, {"items", std::move(item)}, {"minItems", min}, {"maxItems", max}};
}
Json uint_schema() {
    return {{"type", "integer"}, {"minimum", 0}, {"maximum", UINT64_MAX}};
}
Json number_schema(bool nonnegative = true) {
    Json j{{"type", "number"}};
    if (nonnegative)
        j["minimum"] = 0;
    return j;
}
struct Node {
    bool leaf;
    double value;
    size_t feature, right;
    Ns threshold;
};
} // namespace
void register_builtin_formulas(Registry& registry) {
    const auto u = uint_schema(), n = number_schema();
    registry.add_formula("constant/1",
                         canonical(object_schema({{"ns", u}}, {"ns"})),
                         [](const std::string& raw, const auto&) {
                             const Ns ns = cost::parse(raw)["ns"];
                             return [ns](const auto&) {
                                 return ns;
                             };
                         });
    registry.add_formula(
        "nonnegative-linear/1",
        canonical(object_schema({{"intercept_ns", n}, {"coefficients", array_schema(n)}},
                                {"intercept_ns", "coefficients"})),
        [](const std::string& raw, const auto& ranges) {
            auto p = cost::parse(raw);
            const double intercept = p["intercept_ns"];
            auto coefficients = p["coefficients"].template get<std::vector<double>>();
            need(coefficients.size() == ranges.size(), "coefficient/range count mismatch");
            double upper = intercept;
            for (size_t i = 0; i < ranges.size(); ++i)
                upper += coefficients[i] * static_cast<double>(ranges[i].maximum);
            (void)rounded(upper);
            return [intercept, coefficients](const auto& values) {
                double ns = intercept;
                for (size_t i = 0; i < values.size(); ++i)
                    ns += coefficients[i] * static_cast<double>(values[i]);
                return rounded(ns);
            };
        });
    registry.add_formula(
        "upper-bound-table/1",
        canonical(object_schema(
            {{"bins", array_schema(object_schema({{"upper", u}, {"ns", u}}, {"upper", "ns"}), 1)}},
            {"bins"})),
        [](const std::string& raw, const auto& ranges) {
            need(ranges.size() == 1, "upper-bound table requires one feature");
            std::map<Ns, Ns> bins;
            const auto parameters = cost::parse(raw);
            for (const auto& b : parameters["bins"]) {
                const Ns upper = b["upper"];
                need(upper >= ranges[0].minimum && upper <= ranges[0].maximum &&
                         (bins.empty() || upper > bins.rbegin()->first),
                     "invalid table boundary");
                bins.emplace(upper, b["ns"].template get<Ns>());
            }
            need(bins.rbegin()->first == ranges[0].maximum, "incomplete table range");
            return [bins](const auto& values) {
                return bins.lower_bound(values[0])->second;
            };
        });
    const auto split = object_schema({{"feature", u}, {"threshold", u}, {"right", u}},
                                     {"feature", "threshold", "right"});
    registry.add_formula(
        "log-gbdt/1",
        canonical(object_schema(
            {{"initial", number_schema(false)},
             {"rate", n},
             {"trees",
              array_schema(
                  array_schema({{"oneOf", Json::array({number_schema(false), split})}}, 1, 15),
                  1,
                  180)}},
            {"initial", "rate", "trees"})),
        [](const std::string& raw, const auto& ranges) {
            const auto p = cost::parse(raw);
            const double initial = p["initial"], rate = p["rate"];
            need(rate > 0, "GBDT rate must be positive");
            std::vector<std::vector<Node>> trees;
            double magnitude = 0;
            for (const auto& tree : p["trees"]) {
                std::vector<Node> nodes;
                double largest = 0;
                std::function<size_t(size_t, unsigned)> walk = [&](size_t i,
                                                                   unsigned depth) -> size_t {
                    need(i < tree.size(), "incomplete tree");
                    const auto& node = tree[i];
                    if (node.is_number()) {
                        const double leaf = node;
                        largest = std::max(largest, std::abs(leaf));
                        nodes.push_back({true, leaf, 0, 0, 0});
                        return i + 1;
                    }
                    const size_t feature = node["feature"], right = node["right"];
                    const Ns threshold = node["threshold"];
                    need(depth < 3 && feature < ranges.size(), "GBDT depth/feature");
                    need(threshold >= ranges[feature].minimum &&
                             threshold < ranges[feature].maximum,
                         "GBDT threshold outside domain");
                    nodes.push_back({false, 0, feature, right, threshold});
                    need(walk(i + 1, depth + 1) == right, "GBDT right child/preorder mismatch");
                    return walk(right, depth + 1);
                };
                need(walk(0, 0) == tree.size(), "GBDT trailing nodes");
                magnitude += largest;
                trees.push_back(std::move(nodes));
            }
            (void)rounded(std::exp(initial + rate * magnitude));
            return [initial, rate, trees](const auto& values) {
                double sum = 0;
                for (const auto& tree : trees) {
                    size_t i = 0;
                    while (!tree[i].leaf) {
                        const auto& node = tree[i];
                        i = values[node.feature] <= node.threshold ? i + 1 : node.right;
                    }
                    sum += tree[i].value;
                }
                return std::max(Ns{1}, rounded(std::exp(initial + rate * sum)));
            };
        });
    // Exact integer setup + ceil(sum(weight_i * work_i)/rate). Generalizes the
    // existing Host control/distance and Controller compute/buffer formulas.
    registry.add_formula(
        "integer-rate/1",
        canonical(
            object_schema({{"setup_ns", u}, {"units_per_ns", u}, {"weights", array_schema(u)}},
                          {"setup_ns", "units_per_ns", "weights"})),
        [](const std::string& raw, const auto& ranges) {
            const auto p = cost::parse(raw);
            const Ns setup = p["setup_ns"], rate = p["units_per_ns"];
            auto weights = p["weights"].template get<std::vector<Ns>>();
            need(rate > 0 && weights.size() == ranges.size(), "integer rate/count");
            const auto evaluate = [setup, rate, weights](const std::vector<Ns>& values) {
                Ns sum = 0;
                for (size_t i = 0; i < values.size(); ++i) {
                    need(!weights[i] || values[i] <= UINT64_MAX / weights[i],
                         "integer product overflow");
                    const auto v = weights[i] * values[i];
                    need(sum <= UINT64_MAX - v, "integer sum overflow");
                    sum += v;
                }
                const auto ns = sum / rate + (sum % rate != 0);
                need(setup <= UINT64_MAX - ns, "integer setup overflow");
                return setup + ns;
            };
            std::vector<Ns> upper;
            for (const auto& r : ranges)
                upper.push_back(r.maximum);
            (void)evaluate(upper);
            return evaluate;
        });
}
} // namespace ndpsim::cost
