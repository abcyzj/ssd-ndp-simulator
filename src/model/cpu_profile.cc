#include "model/cpu_profile.h"
#include "model/cpu_profile_schema.h"
#include "serialization/json_schema.h"
#include <fstream>

namespace ndpsim::model {
namespace {
using content::Code;
using serialization::Json;
using Key = std::pair<std::string, std::string>;
const std::set<Key> stages{{"INIT", "init"},
                           {"MARK_SCORED", "state"},
                           {"MARK_EXPANDED", "state"},
                           {"SCORE_L2", "score_prepare"},
                           {"SCORE_L2", "score_l2"},
                           {"MERGE_CANDIDATES", "merge"},
                           {"SELECT_FRONTIER", "select"},
                           {"READ_ADJACENCY", "adjacency_prepare"},
                           {"DEDUP_NEIGHBORS", "dedup"},
                           {"FINISH", "finish"}};
const std::map<std::string, uint64_t operators::Work::*, std::less<>> work_fields{
    {"elements", &operators::Work::elements},
    {"flops", &operators::Work::flops},
    {"read_bytes", &operators::Work::read_bytes},
    {"write_bytes", &operators::Work::write_bytes},
    {"output_bytes", &operators::Work::output_bytes},
    {"candidate_checks", &operators::Work::candidate_checks},
    {"comparisons", &operators::Work::comparisons}};
const std::set<std::string> byte_features{
    "read_bytes", "write_bytes", "output_bytes", "copied_bytes", "decoded_bytes"};

void require(bool ok, const std::string& detail, Code code = Code::InvalidArgument) {
    if (!ok)
        throw content::Error(code, "CPU Profile: " + detail);
}
uint64_t rounded(double ns, const std::string& key) {
    const auto ceil = std::ceil(ns);
    require(std::isfinite(ceil) && ns >= 0 && ceil < 0x1p64,
            key + ": service time overflow",
            Code::Overflow);
    return static_cast<uint64_t>(ceil);
}
uint64_t rounded(const Json& ns, const std::string& key) {
    if (ns.is_number_unsigned())
        return ns.get<uint64_t>();
    return rounded(ns.get<double>(), key);
}

struct Feature {
    std::string name;
    uint64_t minimum, maximum;
};
struct Entry {
    std::vector<Feature> features;
    uint64_t constant = 0;
    double intercept = 0;
    std::vector<double> coefficients;
    std::map<uint64_t, uint64_t> bins;
};
} // namespace

struct CpuProfile::Data {
    std::map<Key, Entry> models;
    uint64_t dimensions;
};

CpuProfile CpuProfile::load(const std::string& path,
                            const std::string& expected_sha256,
                            const CpuApplicability& actual,
                            bool allow_fixture) {
    constexpr size_t limit = 16 * 1024 * 1024;
    std::ifstream stream(path, std::ios::binary);
    require(static_cast<bool>(stream), "cannot open " + path);
    std::string raw(limit + 1, '\0');
    stream.read(raw.data(), raw.size());
    require(stream.eof() && !stream.bad(), "read error or metadata budget exceeded: " + path);
    raw.resize(static_cast<size_t>(stream.gcount()));
    require(content::sha256(reinterpret_cast<const uint8_t*>(raw.data()), raw.size()) ==
                expected_sha256,
            "SHA256 mismatch: " + path,
            Code::VersionMismatch);

    Json profile;
    try {
        profile = serialization::parse_unique(raw, [](const auto& key) {
            require(false, "duplicate JSON key: " + key);
        });
    } catch (const Json::exception& e) {
        require(false, std::string("invalid JSON: ") + e.what());
    }
    static const auto schema = Json::parse(embedded::cpu_profile_schema);
    serialization::validate<content::Error>(
        profile,
        schema,
        "",
        schema,
        [](bool ok,
           const std::string& field,
           const std::string& detail,
           const std::string& code = "InvalidArgument") {
            require(ok,
                    field + ": " + detail,
                    code == "Unsupported" ? Code::Unsupported : Code::InvalidArgument);
        });

    const Json expected = {{"hardware", actual.hardware},
                           {"os", actual.os},
                           {"memory_bytes", actual.memory_bytes},
                           {"frequency", actual.frequency},
                           {"affinity", actual.affinity},
                           {"build",
                            {{"source_sha256", actual.source_sha256},
                             {"binary_sha256", actual.binary_sha256},
                             {"compiler", actual.compiler},
                             {"flags", actual.flags}}},
                           {"dimensions", actual.dimensions},
                           {"records", actual.records},
                           {"preparation_context", actual.preparation_context},
                           {"workers", actual.workers}};
    for (const auto& field : expected.items())
        require(profile["applicability"][field.key()] == field.value(),
                "applicability mismatch: " + field.key(),
                Code::VersionMismatch);
    const auto& training = profile["training"];
    if (training["split"] == "fixture")
        require(allow_fixture, "fixture Profile requires explicit fixture permission");
    else
        require(training["query_ids"].size() == 32, "wrong tuning32 size");

    auto data = std::make_shared<Data>();
    data->dimensions = actual.dimensions;
    for (const auto& row : profile["models"]) {
        const Key key{row["operator"], row["stage"]};
        const auto label = key.first + "/" + key.second;
        require(stages.count(key) &&
                    (actual.preparation_context != "resident_random" || key.second != "state"),
                label + ": invalid model key for preparation context");
        auto [it, inserted] = data->models.try_emplace(key);
        require(inserted, label + ": duplicate model key");
        auto& entry = it->second;
        std::set<std::string> names;
        for (const auto& feature : row["features"]) {
            Feature range{feature["name"], feature["minimum"], feature["maximum"]};
            const char* unit = byte_features.count(range.name) ? "B"
                               : range.name == "flops"         ? "flop"
                                                               : "count";
            require(names.insert(range.name).second && feature["unit"] == unit &&
                        range.minimum <= range.maximum,
                    label + ": invalid feature " + range.name);
            entry.features.push_back(std::move(range));
        }

        const auto& model = row["model"];
        if (model["kind"] == "linear") {
            entry.coefficients = model["coefficients"].get<std::vector<double>>();
            require(entry.coefficients.size() == entry.features.size(),
                    label + ": coefficient/feature count mismatch");
            entry.intercept = model["intercept_ns"];
            double upper = entry.intercept;
            for (size_t i = 0; i < entry.features.size(); ++i)
                upper += entry.coefficients[i] * static_cast<double>(entry.features[i].maximum);
            (void)rounded(upper, label);
        } else if (model["kind"] == "table") {
            require(entry.features.size() == 1, label + ": table needs one feature");
            const auto& range = entry.features.front();
            for (const auto& bin : model["bins"]) {
                const auto upper = bin["upper"].get<uint64_t>();
                require(entry.bins.empty() ? upper >= range.minimum
                                           : upper > entry.bins.rbegin()->first,
                        label + ": invalid table boundary");
                entry.bins.emplace(upper, rounded(bin["ns"], label));
            }
            require(entry.bins.rbegin()->first == range.maximum,
                    label + ": incomplete table range");
        } else {
            entry.constant = rounded(model["ns"], label);
        }
    }
    return CpuProfile(std::move(data));
}

uint64_t CpuProfile::estimate(std::string_view op,
                              std::string_view stage,
                              const operators::Work& work,
                              const CpuFeatures& extra) const {
    const Key key{std::string(op), std::string(stage)};
    const auto label = key.first + "/" + key.second;
    require(stages.count(key), label + ": unknown operator/stage", Code::Unsupported);
    const auto found = data_->models.find(key);
    require(found != data_->models.end(), label + ": missing model", Code::Unsupported);
    const auto& entry = found->second;
    double ns = entry.intercept;
    uint64_t value = 0;
    for (size_t i = 0; i < entry.features.size(); ++i) {
        const auto& feature = entry.features[i];
        const auto member = work_fields.find(feature.name);
        const auto supplied = extra.find(feature.name);
        if (member != work_fields.end()) {
            require(supplied == extra.end(),
                    label + ": cannot override Work feature " + feature.name);
            value = work.*(member->second);
        } else if (feature.name == "dimensions") {
            require(supplied == extra.end() || supplied->second == data_->dimensions,
                    label + ": dimensions differ from bound identity",
                    Code::VersionMismatch);
            value = data_->dimensions;
        } else {
            require(supplied != extra.end(), label + ": missing feature " + feature.name);
            value = supplied->second;
        }
        require(feature.minimum <= value && value <= feature.maximum,
                label + ": feature " + feature.name + "=" + std::to_string(value) + " outside [" +
                    std::to_string(feature.minimum) + ", " + std::to_string(feature.maximum) + "]",
                Code::OutOfRange);
        if (!entry.coefficients.empty())
            ns += entry.coefficients[i] * static_cast<double>(value);
    }
    if (!entry.coefficients.empty())
        return rounded(ns, label);
    if (!entry.bins.empty())
        return entry.bins.lower_bound(value)->second;
    return entry.constant;
}
} // namespace ndpsim::model
