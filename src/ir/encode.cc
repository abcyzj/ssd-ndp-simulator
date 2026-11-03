#include "ir/schema.h"
#include <array>
namespace ndpsim::ir {
namespace {
template <class E, size_t N> const char* name(E e, const std::array<const char*, N>& names) {
    auto i = static_cast<size_t>(e);
    if (i >= N)
        throw Error("Unsupported", "/enum", "invalid in-memory enum");
    return names[i];
}
Json identity(const Identity& i) {
    return {{"name", i.name}, {"version", i.version}, {"sha256", i.sha256}};
}
Json type(const Type& t) {
    Json dims = Json::array();
    for (auto d : t.shape)
        dims.push_back({{"extent", d.extent}, {"dynamic", d.dynamic}});
    return {{"name",
             name(t.name,
                  std::array{"u32",
                             "u64",
                             "bool",
                             "ids_u32",
                             "fp32",
                             "scores_id_f64",
                             "adjacency",
                             "query_state",
                             "token"})},
            {"shape", dims}};
}
Json output(const OutputRef& r) {
    return {{"producer_op", r.producer_op}, {"port", r.port}, {"type", type(r.type)}};
}
Json slice(const Slice& s) {
    Json j = {
        {"object_id", s.object_id}, {"content_version", s.content_version}, {"field", s.field}};
    if (auto ids = std::get_if<std::vector<uint32_t>>(&s.indices))
        j["indices"] = *ids;
    else
        j["indices_ref"] = output(std::get<OutputRef>(s.indices));
    if (s.length)
        j["range"] = {{"offset", s.offset}, {"length", s.length}};
    else if (s.offset)
        throw Error("InvalidArgument", "/slice/range", "offset without length");
    return j;
}
Json value(const ValueRef& v) {
    Json j = {{"type", type(v.type)}};
    if (auto s = std::get_if<Slice>(&v.reference)) {
        j["ref"] = "object_slice";
        j["slice"] = slice(*s);
    } else if (auto o = std::get_if<OutputRef>(&v.reference)) {
        j["ref"] = "op_output";
        j["output"] = output(*o);
    } else if (auto s = std::get_if<StateRef>(&v.reference)) {
        j["ref"] = "state_ref";
        j["query"] = s->query;
        j["state_id"] = s->state_id;
        j["version"] = s->version;
    } else {
        j["ref"] = "literal";
        std::visit(
            [&](const auto& x) {
                j["value"] = x;
            },
            std::get<Literal>(v.reference).value);
    }
    return j;
}
void optional(Json& j, const char* key, const Json& value) {
    if (!value.is_null())
        j[key] = value;
}
} // namespace
Json encode(const Document& d) {
    const auto& m = d.manifest;
    Json manifest = {{"ir_version", m.ir_version},
                     {"execution_engine", m.execution_engine},
                     {"execution_mode", m.execution_mode},
                     {"workload_type", m.workload_type},
                     {"frontend", identity(m.frontend)},
                     {"source", identity(m.source)},
                     {"search", m.search},
                     {"mapping", m.mapping},
                     {"dependencies", m.dependencies},
                     {"diagnostics", m.diagnostics},
                     {"operator_set", m.operator_set},
                     {"execution_scope",
                      {{"run", m.run},
                       {"query_identity", "original-id-and-run-ordinal"},
                       {"batch_rule", "query-iteration-semantic-batch-v1"}}}};
    for (auto k : {"dataset", "index", "truth", "queries"})
        manifest[k] = m.artifacts.at(k);
    // Two views of the same catalog identity must agree for hand-built documents.
    if (m.operator_set.at("catalog_sha256") != m.catalog_sha256)
        throw Error(
            "VersionMismatch", "/manifest/operator_set", "in-memory catalog identity mismatch");
    optional(manifest, "extensions", m.extensions);
    Json j = {{"manifest", manifest}, {"objects", Json::array()}, {"operations", Json::array()}};
    for (const auto& o : d.objects) {
        Json obj = {{"object_id", o.object_id},
                    {"object_type", o.object_type},
                    {"content_identity", identity(o.content_identity)},
                    {"content_version", o.content_version},
                    {"element_count", o.element_count},
                    {"element_type",
                     name(o.element_type,
                          std::array{"u32",
                                     "u64",
                                     "bool",
                                     "ids_u32",
                                     "fp32",
                                     "scores_id_f64",
                                     "adjacency",
                                     "query_state",
                                     "token"})},
                    {"shape", {o.width}},
                    {"byte_order", "little"},
                    {"record_schema", o.record_schema},
                    {"logical_layout", "element-field-v1"},
                    {"mutability", "readonly"},
                    {"content_ref", o.content_ref}};
        optional(obj, "extensions", o.extensions);
        j["objects"].push_back(obj);
    }
    for (const auto& o : d.operations)
        j["operations"].push_back(encode(o));
    return j;
}
Json encode(const Operation& o) {

    Json scope = {{"run", o.scope.run},
                  {"query", o.scope.query},
                  {"query_ordinal", o.scope.query_ordinal},
                  {"iteration", o.scope.iteration},
                  {"semantic_batch", o.scope.semantic_batch}};
    if (o.scope.chunk)
        scope["chunk"] = *o.scope.chunk;
    if (o.scope.stage)
        scope["stage"] = *o.scope.stage;
    Json op = {
        {"op_id", o.op_id},
        {"scope", scope},
        {"kind", name(o.kind, std::array{"MEMORY", "COMPUTE", "CONTROL", "SYNC", "EXTERNAL"})},
        {"operator",
         name(o.op,
              std::array{"QUERY_READY",
                         "READ_ADJACENCY",
                         "SCORE_L2",
                         "MERGE_CANDIDATES",
                         "SELECT_FRONTIER",
                         "DEDUP_NEIGHBORS",
                         "JOIN"})},
        {"operator_version", o.operator_version},
        {"placement", name(o.placement, std::array{"host", "controller"})},
        {"inputs", Json::array()},
        {"outputs", Json::array()},
        {"deps", Json::array()},
        {"access", Json::array()},
        {"params", o.params},
        {"workload", o.workload},
        {"effects", o.effects}};
    for (const auto& i : o.inputs)
        op["inputs"].push_back({{"port", i.port}, {"value", value(i.value)}});
    for (const auto& p : o.outputs)
        op["outputs"].push_back(
            {{"name", p.name},
             {"type", type(p.type)},
             {"location",
              name(p.location, std::array{"HostMemory", "DeviceBuffer", "SSDStorage"})}});
    for (const auto& dep : o.deps) {
        Json item = {
            {"producer_op", dep.producer_op},
            {"kind", name(dep.kind, std::array{"ADDRESS", "DATA", "CONTROL", "ORDER"})},
            {"port", dep.port}};
        if (dep.state_version)
            item["state_version"] = dep.state_version;
        op["deps"].push_back(item);
    }
    for (const auto& a : o.access)
        op["access"].push_back(
            {{"slice", slice(a)}, {"mode", "readonly"}, {"order", "input-order"}});
    optional(op, "schedule", o.schedule);
    optional(op, "provenance", o.provenance);
    optional(op, "extensions", o.extensions);
    return op;
}
void validate(const Document& d) {
    (void)parse(encode(d));
}
} // namespace ndpsim::ir
