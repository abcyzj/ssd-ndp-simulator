#include "mapping/memory.h"
#include <fstream>
#include <set>
namespace ndpsim::memory {
namespace {
using Json = nlohmann::json;
void need(bool b, const std::string& s) {
    if (!b)
        throw std::runtime_error("memory: " + s);
}
std::string read(const std::filesystem::path& p) {
    std::ifstream f(p, std::ios::binary);
    need(bool(f), "cannot open " + p.string());
    std::string s;
    char block[8192];
    while (f.read(block, sizeof(block)) || f.gcount()) {
        s.append(block, f.gcount());
        need(s.size() <= 64 * 1024 * 1024, "file budget");
    }
    need(f.eof(), "read failure");
    return s;
}
Json parse(const std::string& s) {
    std::vector<std::set<std::string>> keys;
    return Json::parse(s, [&keys](int depth, Json::parse_event_t e, Json& v) {
        need(depth <= 64, "JSON depth");
        if (e == Json::parse_event_t::object_start)
            keys.emplace_back();
        if (e == Json::parse_event_t::key)
            need(keys.back().insert(v.get<std::string>()).second, "duplicate JSON key");
        if (e == Json::parse_event_t::object_end)
            keys.pop_back();
        return true;
    });
}
uint64_t positive(const Json& j) {
    need(j.is_number_unsigned() && j.get<uint64_t>() > 0, "positive integer quota required");
    return j.get<uint64_t>();
}
} // namespace
Plan::Plan(const std::filesystem::path& path) {
    const auto raw = read(path);
    auto c = parse(raw);
    const std::set<std::string> fields = {"format",
                                          "package",
                                          "scratch",
                                          "report",
                                          "report_sha256",
                                          "package_sha256",
                                          "registry",
                                          "registry_sha256",
                                          "semantic_report",
                                          "semantic_sha256",
                                          "allow_fixture",
                                          "profile",
                                          "profile_sha256",
                                          "target",
                                          "context",
                                          "workers",
                                          "slots",
                                          "admission",
                                          "output"};
    need(c.is_object(), "config object required");
    for (auto i = c.begin(); i != c.end(); ++i)
        need(fields.count(i.key()), "unknown config field " + i.key());
    need(c.at("format") == "ndpsim-memory-run/1", "unsupported format");
    auto resolve = [&](const std::string& k) {
        return std::filesystem::absolute(path).parent_path() / c.at(k).get<std::string>();
    };
    inputs.package = resolve("package");
    inputs.scratch = resolve("scratch");
    inputs.receipt.report = resolve("report");
    inputs.receipt.report_sha256 = c.at("report_sha256");
    inputs.receipt.package_sha256 = c.at("package_sha256");
    inputs.receipt.registry_sha256 = c.at("registry_sha256");
    inputs.receipt.allow_fixture = c.value("allow_fixture", false);
    need(c.contains("semantic_report") == c.contains("semantic_sha256"),
         "semantic report/hash pair required");
    if (c.contains("semantic_report")) {
        inputs.receipt.semantic_report = resolve("semantic_report");
        inputs.receipt.semantic_sha256 = c.at("semantic_sha256");
    }
    inputs.config.workers = positive(c.at("workers"));
    inputs.config.admission = positive(c.at("admission"));
    slots = positive(c.at("slots"));
    output = resolve("output");
    need(!std::filesystem::exists(output), "output must be new");
    auto reg = parse(read(resolve("registry")));
    need(capture::sha256(reg.dump(-1, ' ', true) + "\n") == inputs.receipt.registry_sha256,
         "registry hash mismatch");
    cost::register_builtin_formulas(registry_);
    for (const auto& s : reg.at("operators")) {
        capture::OperatorSpec op;
        op.id = s.at("id");
        op.version = s.at("version");
        op.locations = s.at("locations").get<std::vector<std::string>>();
        op.semantic_validator = s.at("semantic_validator");
        for (const auto& st : s.at("stages")) {
            capture::StageSpec spec;
            spec.kind = st.at("kind");
            spec.contexts = st.at("contexts").get<std::vector<std::string>>();
            spec.output_size_rule = st.at("output_size_rule");
            spec.work_units = st.at("work_units").get<std::map<std::string, std::string>>();
            spec.feature_units = st.at("feature_units").get<std::map<std::string, std::string>>();
            op.stages.push_back(spec);
        }
        registry_.add_operator(op);
    }
    auto t = c.at("target");
    need(t.is_object() && t.size() == 6 && t.at("memory_bytes").is_number_unsigned(),
         "target fields/types");
    target_ = {t.at("id"),
               t.at("hardware"),
               t.at("os"),
               t.at("frequency"),
               t.at("affinity"),
               t.at("memory_bytes")};
    context_ = c.at("context");
    need(context_ == "resident_random/2" ||
             (inputs.receipt.allow_fixture && context_ == "fixture/1"),
         "unsupported memory context");
    const auto pr = read(resolve("profile"));
    profile_ = std::make_unique<cost::Profile>(cost::Profile::parse(
        pr, c.at("profile_sha256"), registry_, target_, inputs.receipt.allow_fixture));
    rules_ = parse(pr).at("rules");
    auto manifest = parse(read(inputs.package / "manifest.json"));
    binding_ = {{"package_sha256", inputs.receipt.package_sha256},
                {"provenance", manifest.at("provenance")},
                {"target", t},
                {"context", context_},
                {"workers", inputs.config.workers},
                {"slots", slots},
                {"admission", inputs.config.admission}};
    snapshot = {{"config_sha256", capture::sha256(raw)},
                {"config", c},
                {"effective_paths",
                 {{"package", inputs.package.string()},
                  {"scratch", inputs.scratch.string()},
                  {"report", inputs.receipt.report.string()},
                  {"profile", resolve("profile").string()},
                  {"registry", resolve("registry").string()},
                  {"output", output.string()}}},
                {"scheduler", "FIFO/offered=0"},
                {"binding", binding_},
                {"registry", reg},
                {"profile", parse(pr)}};
}
timing::Operation Plan::lower(const dag::Stage& s, timing::HostCpu& cpu) {
    need(s.metadata.context == context_, "stage context mismatch");
    cost::Input in;
    in.key = s.key;
    in.operator_id = s.operator_id;
    in.operator_version = s.operator_version;
    in.stage_kind = s.metadata.kind;
    in.context = context_;
    in.location = "host";
    in.work = s.metadata.work;
    in.features = s.metadata.features;
    in.target = target_;
    in.binding_json = binding_.dump();
    auto ns = profile_->demand(in);
    timing::Operation op;
    op.key = s.key;
    op.work = in.work;
    op.features = in.features;
    op.service_key =
        Json({{"query", s.key.query}, {"op", s.key.op}, {"stage", s.key.stage}}).dump();
    op.outputs = s.metadata.outputs;
    op.resource = &cpu;
    op.duration_ns = ns;
    for (const auto& r : rules_)
        if (r.at("operator") == in.operator_id && r.at("operator_version") == in.operator_version &&
            r.at("stage_kind") == in.stage_kind && r.at("context") == context_ &&
            r.at("location") == "host") {
            last_demand = {{"query", s.key.query},
                           {"op", s.key.op},
                           {"stage", s.key.stage},
                           {"operator", s.operator_id},
                           {"stage_kind", s.metadata.kind},
                           {"work", Json::object()},
                           {"features", Json::object()},
                           {"duration_ns", ns},
                           {"source", r.at("source")},
                           {"formula", r.value("formula", "measured-table")}};
            for (const auto& p : in.work)
                last_demand["work"][p.first] = {{"value", p.second.first},
                                                {"unit", p.second.second}};
            for (const auto& p : in.features)
                last_demand["features"][p.first] = {{"value", p.second.first},
                                                    {"unit", p.second.second}};
            return op;
        }
    throw std::runtime_error("memory: missing provenance rule");
}
} // namespace ndpsim::memory
