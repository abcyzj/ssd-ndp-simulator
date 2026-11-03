#include "ir/schema.h"
#include "ir/embedded.h"
#include "content/content.h"
#include <algorithm>
#include <fstream>
#include <limits>
#include <map>
#include <regex>
#include <set>

namespace ndpsim::ir {
Error::Error(std::string c, std::string p, std::string d, uint64_t q, uint64_t o, uint64_t r)
    : std::runtime_error(std::move(d)), code(std::move(c)), path(std::move(p)), query(q), op(o),
      producer(r) {
}
Json Error::diagnostic() const {
    return {{"code", code},
            {"path", path},
            {"query", query},
            {"ir_op", op},
            {"producer", producer},
            {"time_ns", nullptr},
            {"cause", "IR schema validation"},
            {"detail", what()}};
}
namespace {
[[noreturn]] void
fail(const std::string& p, const std::string& d, const std::string& c = "InvalidArgument") {
    throw Error(c, p, d);
}
void require(bool ok,
             const std::string& p,
             const std::string& d,
             const std::string& c = "InvalidArgument") {
    if (!ok)
        fail(p, d, c);
}
// The generated schema uses only this explicitly implemented JSON Schema subset.
// Tests independently validate fixtures with Draft202012Validator when available.
void validate(const Json& v, const Json& s, const std::string& p) {
    if (s.contains("$ref")) {
        validate(v, schema().at(Json::json_pointer(s["$ref"].get<std::string>().substr(1))), p);
        return;
    }
    if (s.contains("oneOf")) {
        // Tagged unions retain the selected branch's precise diagnostic.
        for (const auto& tag : {"operator", "ref"})
            if (v.is_object() && v.contains(tag)) {
                for (const auto& branch : s["oneOf"])
                    if (branch.contains("properties") && branch["properties"].contains(tag) &&
                        branch["properties"][tag].contains("const") &&
                        branch["properties"][tag]["const"] == v[tag]) {
                        validate(v, branch, p);
                        return;
                    }
            }
        size_t n = 0;
        for (const auto& branch : s["oneOf"]) {
            try {
                validate(v, branch, p);
                ++n;
            } catch (const Error&) {
            }
        }
        require(n == 1, p, "expected exactly one schema alternative");
        return;
    }
    if (s.contains("const"))
        require(v == s["const"],
                p,
                "unknown version, semantic constant or operator combination",
                "Unsupported");
    if (s.contains("enum"))
        require(std::find(s["enum"].begin(), s["enum"].end(), v) != s["enum"].end(),
                p,
                "unknown enum value",
                "Unsupported");
    if (!s.contains("type"))
        return;
    const auto t = s["type"].get<std::string>();
    if (t == "object") {
        require(v.is_object(), p, "expected object");
        if (s.contains("required"))
            for (const auto& k : s["required"])
                require(
                    v.contains(k.get<std::string>()), p, "missing field: " + k.get<std::string>());
        for (auto i = v.begin(); i != v.end(); ++i) {
            if (s.contains("properties") && s["properties"].contains(i.key()))
                validate(i.value(), s["properties"][i.key()], p + "/" + i.key());
            else
                require(s.value("additionalProperties", true),
                        p + "/" + i.key(),
                        "unknown semantic field",
                        "Unsupported");
        }
    } else if (t == "array") {
        require(v.is_array(), p, "expected array");
        require(v.size() >= s.value("minItems", size_t(0)), p, "array too short");
        if (s.contains("maxItems"))
            require(v.size() <= s["maxItems"].get<size_t>(), p, "array too long");
        for (size_t i = 0; i < v.size(); ++i)
            validate(v[i], s.at("items"), p + "/" + std::to_string(i));
    } else if (t == "string") {
        require(v.is_string(), p, "expected string");
        const auto str = v.get<std::string>();
        require(str.size() >= s.value("minLength", size_t(0)), p, "empty string");
        if (s.contains("pattern"))
            require(std::regex_match(str, std::regex(s["pattern"].get<std::string>())),
                    p,
                    "invalid identifier/hash");
    } else if (t == "boolean")
        require(v.is_boolean(), p, "expected boolean");
    else if (t == "integer") {
        require(v.is_number_integer() && !v.is_boolean(), p, "expected exact unsigned integer");
        require(v.is_number_unsigned() || v.get<int64_t>() >= 0, p, "negative integer");
        const auto n = v.get<uint64_t>();
        if (s.contains("minimum"))
            require(n >= s["minimum"].get<uint64_t>(), p, "integer below minimum", "OutOfRange");
        if (s.contains("maximum"))
            require(n <= s["maximum"].get<uint64_t>(), p, "integer above maximum", "OutOfRange");
    } else
        fail(p, "unimplemented schema keyword type", "Unsupported");
}
template <class E> E choice(const Json& v, std::initializer_list<const char*> names) {
    int n = 0;
    for (auto name : names) {
        if (v == name)
            return static_cast<E>(n);
        ++n;
    }
    fail("/", "invalid enum cast", "Unsupported");
}
Type type(const Json& j) {
    Type t{choice<TypeName>(j["name"],
                            {"u32",
                             "u64",
                             "bool",
                             "ids_u32",
                             "fp32",
                             "scores_id_f64",
                             "adjacency",
                             "query_state",
                             "token"}),
           {}};
    const bool scalar = t.name == TypeName::U32 || t.name == TypeName::U64 ||
                        t.name == TypeName::Bool || t.name == TypeName::QueryState ||
                        t.name == TypeName::Token;
    require(j["shape"].size() == (scalar ? 0 : 1), "/type/shape", "type rank mismatch");
    for (auto& dim : j["shape"])
        t.shape.push_back({dim["extent"], dim["dynamic"]});
    uint64_t width = t.name == TypeName::ScoresIdF64 ? 12 : 4;
    for (auto dim : t.shape)
        require(dim.extent <= UINT64_MAX / width, "/type/shape", "byte size overflow", "Overflow");
    return t;
}
Identity identity(const Json& j) {
    return {j["name"], j["version"], j["sha256"]};
}
OutputRef output(const Json& j) {
    return {j["producer_op"], j["port"], type(j["type"])};
}
Slice slice(const Json& j) {
    require(j.contains("indices") != j.contains("indices_ref"),
            "/slice",
            "exactly one indices or indices_ref required");
    Slice s{j["object_id"], j["field"], j["content_version"], std::vector<uint32_t>{}, 0, 0};
    if (j.contains("indices"))
        s.indices = j["indices"].get<std::vector<uint32_t>>();
    else
        s.indices = output(j["indices_ref"]);
    if (j.contains("range")) {
        s.offset = j["range"]["offset"];
        s.length = j["range"]["length"];
        require(s.offset <= UINT64_MAX - s.length, "/slice/range", "range overflow", "Overflow");
    }
    return s;
}
ValueRef value(const Json& j) {
    ValueRef v{type(j["type"]), Literal{uint64_t(0)}};
    if (j["ref"] == "object_slice")
        v.reference = slice(j["slice"]);
    else if (j["ref"] == "op_output") {
        auto o = output(j["output"]);
        require(j["type"] == j["output"]["type"], "/value/type", "reference type mismatch");
        v.reference = o;
    } else if (j["ref"] == "state_ref") {
        require(v.type.name == TypeName::QueryState, "/state_ref", "expected query_state");
        v.reference = StateRef{j["query"], j["state_id"], j["version"]};
    } else {
        require(v.type.name == TypeName::U32 || v.type.name == TypeName::U64 ||
                    v.type.name == TypeName::Bool || v.type.name == TypeName::IdsU32,
                "/literal",
                "only small scalar configuration literals are supported",
                "Unsupported");
        require(j["value"].is_boolean() == (v.type.name == TypeName::Bool),
                "/literal",
                "literal type mismatch");
        if (v.type.name == TypeName::IdsU32) {
            require(j["value"].is_array() && j["value"].size() == 1 &&
                        v.type.shape[0].extent == 1 && !v.type.shape[0].dynamic,
                    "/literal",
                    "only a single static entry ID is permitted");
            v.reference = Literal{j["value"].get<std::vector<uint32_t>>()};
        } else if (v.type.name == TypeName::Bool)
            v.reference = Literal{j["value"].get<bool>()};
        else {
            auto n = j["value"].get<uint64_t>();
            require(v.type.name != TypeName::U32 || n <= UINT32_MAX,
                    "/literal",
                    "u32 overflow",
                    "Overflow");
            v.reference = Literal{n};
        }
    }
    return v;
}
} // namespace
const Json& schema() {
    static const Json j = Json::parse(embedded::schema);
    return j;
}
const Json& capabilities() {
    static const Json j = Json::parse(embedded::catalog);
    return j;
}
std::string catalog_sha256() {
    auto bytes = capabilities().dump();
    return content::sha256(reinterpret_cast<const uint8_t*>(bytes.data()), bytes.size());
}
Json read_json(const std::string& path) {
    std::ifstream f(path);
    if (!f)
        fail(path, "cannot open input");
    std::vector<std::set<std::string>> keys;
    auto callback = [&](int, Json::parse_event_t event, Json& parsed) {
        if (event == Json::parse_event_t::object_start)
            keys.emplace_back();
        if (event == Json::parse_event_t::key)
            require(keys.back().insert(parsed.get<std::string>()).second,
                    path,
                    "duplicate JSON key: " + parsed.get<std::string>());
        if (event == Json::parse_event_t::object_end)
            keys.pop_back();
        return true;
    };
    try {
        return Json::parse(f, callback);
    } catch (const Json::exception& e) {
        fail(path, e.what());
    }
}
Document parse(const Json& j) {
    try {
        validate(j, schema(), "");
    } catch (Error& e) {
        if (e.path.rfind("/operations/", 0) == 0 && j.contains("operations") &&
            j["operations"].is_array()) {
            auto begin = std::string("/operations/").size();
            auto index = e.path.substr(begin, e.path.find('/', begin) - begin);
            if (!index.empty() && index.find_first_not_of("0123456789") == std::string::npos) {
                auto n = std::stoull(index);
                if (n < j["operations"].size()) {
                    const auto& o = j["operations"][n];
                    if (o.contains("op_id") && o["op_id"].is_number_unsigned())
                        e.op = o["op_id"];
                    if (o.contains("scope") && o["scope"].is_object() &&
                        o["scope"].contains("query") && o["scope"]["query"].is_number_unsigned())
                        e.query = o["scope"]["query"];
                }
            }
        }
        throw;
    }
    const auto& m = j["manifest"];
    require(m["operator_set"]["catalog_sha256"] == catalog_sha256(),
            "/manifest/operator_set/catalog_sha256",
            "capability catalog identity mismatch",
            "VersionMismatch");
    Manifest manifest{m["ir_version"],
                      m["execution_engine"],
                      m["execution_mode"],
                      m["workload_type"],
                      m["execution_scope"]["run"],
                      m["operator_set"]["catalog_sha256"],
                      identity(m["frontend"]),
                      identity(m["source"]),
                      Json::object(),
                      m["search"],
                      m["mapping"],
                      m["dependencies"],
                      m["diagnostics"],
                      m["operator_set"],
                      m.value("extensions", Json())};
    for (auto k : {"dataset", "index", "truth", "queries"})
        manifest.artifacts[k] = m[k];
    Document doc{std::move(manifest), {}, {}};
    require(m["dataset"]["shape"].size() == 2 && m["queries"]["shape"].size() == 2 &&
                m["index"]["shape"].size() == 2 && m["truth"]["shape"].size() == 2,
            "/manifest",
            "artifact shapes must be rank two");
    require(m["dataset"]["shape"][1] == m["queries"]["shape"][1] &&
                m["dataset"]["shape"][0] == m["index"]["shape"][0],
            "/manifest",
            "dataset/query/index shape mismatch");
    require(m["truth"]["shape"][0] == m["queries"]["shape"][0] &&
                m["truth"]["shape"][1] >= m["search"]["k"],
            "/manifest/truth",
            "truth shape does not cover queries/k");
    require(m["search"]["entry"].get<uint64_t>() < m["dataset"]["shape"][0].get<uint64_t>(),
            "/manifest/search/entry",
            "entry outside dataset",
            "OutOfRange");
    std::set<std::string> objects, declared;
    for (const auto& o : m["operator_set"]["operators"])
        require(declared.insert(o["operator"].get<std::string>()).second,
                "/manifest/operator_set",
                "duplicate operator identity");
    for (const auto& o : j["objects"]) {
        const std::string p = "/objects/" + o["object_id"].get<std::string>();
        require(objects.insert(o["object_id"].get<std::string>()).second, p, "duplicate object ID");
        require(o["element_count"].get<uint64_t>() <= uint64_t(UINT32_MAX) + 1,
                p,
                "current backend supports only uint32 element IDs",
                "Unsupported");
        require(o["shape"].size() == 1, p, "object record must have rank one");
        const bool adj = o["object_type"] == "adjacency";
        require(o["element_type"] == (adj ? "u32" : "fp32") &&
                    o["record_schema"] == (adj ? "degree-ids-u32-v1" : "fp32-vector-v1"),
                p,
                "record/type mismatch");
        uint64_t width = o["shape"][0];
        require(width <= UINT32_MAX && width <= (UINT64_MAX / 4) - (adj ? 1 : 0),
                p,
                "record width overflow",
                "Overflow");
        require(o["element_count"].get<uint64_t>() <= UINT64_MAX / (4 * (width + (adj ? 1 : 0))),
                p,
                "object byte size overflow",
                "Overflow");
        const auto& a = m[adj ? "index" : "dataset"]["identity"];
        require(o["content_identity"] == a,
                p,
                "object content identity not bound to manifest",
                "VersionMismatch");
        const auto& shape = m[o["object_type"] == "query_vectors" ? "queries"
                              : adj                               ? "index"
                                                                  : "dataset"]["shape"];
        require(shape.size() == 2 && shape[0] == o["element_count"] && shape[1] == width,
                p,
                "object shape differs from bound artifact");
        doc.objects.push_back({o["object_id"],
                               o["object_type"],
                               o["content_ref"],
                               o["record_schema"],
                               identity(o["content_identity"]),
                               o["content_version"],
                               o["element_count"],
                               width,
                               adj ? TypeName::U32 : TypeName::Fp32,
                               o.value("extensions", Json())});
    }
    uint64_t last = 0;
    for (const auto& o : j["operations"]) {
        uint64_t related = 0;
        try {
            require(o["op_id"].get<uint64_t>() > last,
                    "/op_id",
                    "operation IDs must increase without reuse");
            last = o["op_id"];
            require(o["scope"]["run"] == doc.manifest.run, "/scope/run", "wrong run identity");
            require(o["scope"]["query_ordinal"].get<uint64_t>() <
                        m["queries"]["shape"][0].get<uint64_t>(),
                    "/scope/query_ordinal",
                    "query ordinal outside bound query set",
                    "OutOfRange");
            require(declared.count(o["operator"]), "/operator", "operator missing from manifest");
            const auto& entries = capabilities()["operators"];
            auto cap = std::find_if(entries.begin(), entries.end(), [&](const Json& c) {
                return c["operator"] == o["operator"];
            });
            require(cap != entries.end(), "/operator", "unregistered operator", "Unsupported");
            require(o["workload"]["rule"] == (*cap)["workload_rule"] &&
                        o["effects"] == (*cap)["effects"],
                    "/workload",
                    "effect or workload rule mismatch");
            require(!o["workload"].contains("counts"),
                    "/workload/counts",
                    "trusted evaluate owns actual counts; submitted counts unsupported",
                    "Unsupported");
            require(o["placement"] == m["mapping"]["placement"] || o["operator"] != "SCORE_L2",
                    "/placement",
                    "score placement differs from manifest");
            Operation op{
                o["op_id"],
                {o["scope"]["run"],
                 o["scope"]["query"],
                 o["scope"]["query_ordinal"],
                 o["scope"]["iteration"],
                 o["scope"]["semantic_batch"],
                 {},
                 {}},
                choice<Kind>(o["kind"], {"MEMORY", "COMPUTE", "CONTROL", "SYNC", "EXTERNAL"}),
                choice<Operator>(o["operator"],
                                 {"QUERY_READY",
                                  "READ_ADJACENCY",
                                  "SCORE_L2",
                                  "MERGE_CANDIDATES",
                                  "SELECT_FRONTIER",
                                  "DEDUP_NEIGHBORS",
                                  "JOIN"}),
                o["operator_version"],
                choice<Placement>(o["placement"], {"host", "controller"}),
                {},
                {},
                {},
                {},
                o["params"],
                o["workload"],
                o["effects"].get<std::vector<std::string>>(),
                o.value("schedule", Json()),
                o.value("provenance", Json()),
                o.value("extensions", Json())};
            if (o["scope"].contains("chunk"))
                op.scope.chunk = o["scope"]["chunk"].get<uint64_t>();
            if (o["scope"].contains("stage"))
                op.scope.stage = o["scope"]["stage"].get<std::string>();
            std::set<std::string> ins, outs;
            for (const auto& i : o["inputs"]) {
                require(ins.insert(i["port"].get<std::string>()).second,
                        "/inputs",
                        "duplicate input port");
                auto v = value(i["value"]);
                if (auto state = std::get_if<StateRef>(&v.reference))
                    require(
                        state->query == op.scope.query, "/state_ref/query", "cross-query state");
                op.inputs.push_back({i["port"], std::move(v)});
            }
            for (const auto& p : o["outputs"]) {
                require(outs.insert(p["name"].get<std::string>()).second,
                        "/outputs",
                        "duplicate output port");
                require(p["location"] == "HostMemory",
                        "/outputs/location",
                        "registered operators publish only HostMemory",
                        "Unsupported");
                op.outputs.push_back(
                    {p["name"],
                     type(p["type"]),
                     choice<Location>(p["location"],
                                      {"HostMemory", "DeviceBuffer", "SSDStorage"})});
            }
            for (const auto& side : {"inputs", "outputs"}) {
                const auto& expected = (*cap)[side];
                const auto& actual = o[side];
                // JOIN accepts a variable set of typed producer outputs; other ports are fixed.
                if (op.op == Operator::Join && std::string(side) == "inputs")
                    continue;
                require(actual.size() == expected.size(),
                        "/" + std::string(side),
                        "operator port count mismatch");
                for (const auto& e : expected) {
                    auto it = std::find_if(actual.begin(), actual.end(), [&](const Json& a) {
                        return a[std::string(side) == "inputs" ? "port" : "name"] == e["port"];
                    });
                    require(it != actual.end(), "/" + std::string(side), "missing registered port");
                    const auto& t =
                        std::string(side) == "inputs" ? (*it)["value"]["type"] : (*it)["type"];
                    require(t["name"] == e["type"],
                            "/" + std::string(side),
                            "operator port type mismatch");
                }
            }
            std::set<std::string> edges;
            for (const auto& d : o["deps"])
                require(edges.insert(d.dump()).second, "/deps", "duplicate dependency");
            for (const auto& d : o["deps"])
                op.deps.push_back(
                    {d["producer_op"],
                     choice<DependencyKind>(d["kind"], {"ADDRESS", "DATA", "CONTROL", "ORDER"}),
                     d["port"],
                     d.value("state_version", uint64_t(0))});
            for (const auto& a : o["access"])
                op.access.push_back(slice(a["slice"]));
            require(op.access.size() ==
                        (op.op == Operator::ReadAdjacency || op.op == Operator::ScoreL2 ? 1 : 0),
                    "/access",
                    "logical read effect must declare exactly one slice");
            auto check_slice = [&](const Slice& s) {
                auto it =
                    std::find_if(doc.objects.begin(), doc.objects.end(), [&](const DataObject& x) {
                        return x.object_id == s.object_id;
                    });
                require(it != doc.objects.end(), "/slice/object_id", "unknown logical object");
                require(it->content_version == s.content_version,
                        "/slice/content_version",
                        "content version mismatch",
                        "VersionMismatch");
                require(s.field == (it->object_type == "adjacency" ? "neighbors" : "vector"),
                        "/slice/field",
                        "field/type mismatch");
                require(s.offset <= it->width && s.length <= it->width - s.offset,
                        "/slice/range",
                        "range outside record",
                        "OutOfRange");
                if (auto ids = std::get_if<std::vector<uint32_t>>(&s.indices))
                    for (auto id : *ids)
                        require(id < it->element_count,
                                "/slice/indices",
                                "element outside object",
                                "OutOfRange");
                else
                    require(std::get<OutputRef>(s.indices).type.name == TypeName::IdsU32,
                            "/slice/indices_ref",
                            "address requires ids_u32");
            };
            for (auto& a : op.access)
                check_slice(a);
            for (auto& i : op.inputs)
                if (auto s = std::get_if<Slice>(&i.value.reference))
                    check_slice(*s);
            if (!op.access.empty()) {
                const auto& a = op.access.front();
                auto it =
                    std::find_if(doc.objects.begin(), doc.objects.end(), [&](const DataObject& x) {
                        return x.object_id == a.object_id;
                    });
                require(it->object_type ==
                            (op.op == Operator::ScoreL2 ? "base_vectors" : "adjacency"),
                        "/access",
                        "wrong read object kind");
                if (op.op == Operator::ScoreL2) {
                    require(o["params"]["dimensions"] == it->width,
                            "/params/dimensions",
                            "vector dimensions mismatch");
                    require(o["params"]["chunk"] == m["search"]["chunk"],
                            "/params/chunk",
                            "search chunk mismatch");
                    for (const auto& i : o["inputs"])
                        if (i["port"] == "query")
                            require(i["value"]["type"]["shape"][0]["extent"] == it->width,
                                    "/inputs/query",
                                    "query dimensions mismatch");
                }
            }
            if (op.op == Operator::SelectFrontier)
                for (auto k : {"L", "B", "E"})
                    require(o["params"][k] == m["search"][k],
                            "/params",
                            "frozen search parameter mismatch");
            auto extent = [&](const char* side, const char* port) -> uint64_t {
                for (const auto& x : o[side])
                    if (x[std::string(side) == "inputs" ? "port" : "name"] == port) {
                        const auto& t =
                            std::string(side) == "inputs" ? x["value"]["type"] : x["type"];
                        return t["shape"][0]["extent"].get<uint64_t>();
                    }
                fail("/shape", "missing shaped port");
            };
            if (op.op == Operator::QueryReady)
                require(extent("outputs", "query") == m["dataset"]["shape"][1].get<uint64_t>(),
                        "/outputs/query",
                        "query dimension differs from dataset");
            if (op.op == Operator::ScoreL2)
                require(extent("outputs", "scores") == extent("inputs", "ids"),
                        "/outputs/scores",
                        "score count differs from ID bound");
            if (op.op == Operator::ReadAdjacency)
                require(extent("outputs", "adjacency") == extent("inputs", "ids"),
                        "/outputs/adjacency",
                        "adjacency record count differs from ID bound");
            if (op.op == Operator::MergeCandidates) {
                auto a = extent("inputs", "previous"), b = extent("inputs", "batch");
                require(
                    a <= UINT64_MAX - b, "/outputs/scores", "merged shape overflow", "Overflow");
                require(extent("outputs", "scores") <= a + b,
                        "/outputs/scores",
                        "merged scores exceed input bound");
            }
            if (op.op == Operator::SelectFrontier)
                require(extent("outputs", "ids") <= std::min(m["search"]["B"].get<uint64_t>(),
                                                             m["search"]["E"].get<uint64_t>()),
                        "/outputs/ids",
                        "frontier exceeds B/E bound");
            if (op.op == Operator::DedupNeighbors) {
                auto n = extent("inputs", "adjacency"),
                     degree = m["index"]["shape"][1].get<uint64_t>();
                require(n <= UINT64_MAX / degree,
                        "/outputs/ids",
                        "neighbor shape overflow",
                        "Overflow");
                require(extent("outputs", "ids") <= n * degree,
                        "/outputs/ids",
                        "neighbor output exceeds edge bound");
            }
            auto port_type = [&](const char* side, const char* port) -> Json {
                for (const auto& x : o[side])
                    if (x[std::string(side) == "inputs" ? "port" : "name"] == port)
                        return std::string(side) == "inputs" ? x["value"]["type"] : x["type"];
                fail("/type", "registered port missing");
            };
            if (op.op == Operator::ScoreL2 || op.op == Operator::ReadAdjacency) {
                const char* result = op.op == Operator::ScoreL2 ? "scores" : "adjacency";
                require(port_type("outputs", result)["shape"] ==
                            port_type("inputs", "ids")["shape"],
                        "/outputs/shape",
                        "read/score result must preserve address batch shape");
                const auto& address = op.access.front();
                if (address.length) {
                    auto object = std::find_if(
                        doc.objects.begin(), doc.objects.end(), [&](const DataObject& x) {
                            return x.object_id == address.object_id;
                        });
                    require(address.offset == 0 && address.length == object->width,
                            "/access/range",
                            "registered read/score requires the complete logical field",
                            "Unsupported");
                }
                if (auto ids = std::get_if<std::vector<uint32_t>>(&address.indices)) {
                    auto t = port_type("inputs", "ids");
                    auto bound = t["shape"][0]["extent"].get<uint64_t>();
                    require(t["shape"][0]["dynamic"].get<bool>() ? ids->size() <= bound
                                                                 : ids->size() == bound,
                            "/access/shape",
                            "static address count differs from declared ID shape");
                }
            }
            if (op.op == Operator::MergeCandidates || op.op == Operator::SelectFrontier ||
                op.op == Operator::DedupNeighbors)
                require(o["outputs"][0]["type"]["shape"][0]["dynamic"].get<bool>(),
                        "/outputs/shape",
                        "variable-length control result needs dynamic shape");
            if (op.op == Operator::QueryReady || op.op == Operator::ScoreL2) {
                auto t = port_type(op.op == Operator::QueryReady ? "outputs" : "inputs", "query");
                require(!t["shape"][0]["dynamic"].get<bool>(),
                        "/query/shape",
                        "query vector dimension must be fixed");
            }
            // Diagnostic documents are ordered submission descriptions, not replay files.
            // Check references available in this document; runtime ownership/admission is V03-2.
            auto check_ref = [&](const Json& r, const char* dependency) {
                uint64_t producer = r["producer_op"];
                related = producer;
                auto previous = std::find_if(
                    j["operations"].begin(), j["operations"].end(), [&](const Json& x) {
                        return x["op_id"] == producer;
                    });
                require(producer < op.op_id && previous != j["operations"].end(),
                        "/reference",
                        "missing, self or future producer");
                require((*previous)["scope"]["run"] == o["scope"]["run"] &&
                            (*previous)["scope"]["query"] == o["scope"]["query"] &&
                            (*previous)["scope"]["query_ordinal"] == o["scope"]["query_ordinal"],
                        "/reference/scope",
                        "cross-query producer");
                auto port = std::find_if((*previous)["outputs"].begin(),
                                         (*previous)["outputs"].end(),
                                         [&](const Json& x) {
                                             return x["name"] == r["port"];
                                         });
                require(port != (*previous)["outputs"].end(),
                        "/reference/port",
                        "producer port missing");
                require((*port)["type"] == r["type"],
                        "/reference/type",
                        "producer type/shape mismatch");
                bool bound = false;
                for (const auto& d : o["deps"])
                    if (d["producer_op"] == producer && d["port"] == r["port"] &&
                        d["kind"] == dependency)
                        bound = true;
                require(bound, "/deps", std::string("missing ") + dependency + " dependency");
            };
            for (const auto& i : o["inputs"]) {
                const auto& v = i["value"];
                if (v["ref"] == "op_output")
                    check_ref(v["output"],
                              (op.op == Operator::ScoreL2 || op.op == Operator::ReadAdjacency) &&
                                      i["port"] == "ids"
                                  ? "ADDRESS"
                                  : "DATA");
                if (v["ref"] == "object_slice") {
                    require((op.op == Operator::ScoreL2 || op.op == Operator::ReadAdjacency) &&
                                i["port"] == "ids",
                            "/inputs",
                            "logical slice does not grant DATA residency");
                    if (v["slice"].contains("indices_ref"))
                        check_ref(v["slice"]["indices_ref"], "ADDRESS");
                }
                if (v["ref"] == "literal" && v["type"]["name"] == "ids_u32")
                    require(v["value"][0] == m["search"]["entry"],
                            "/literal",
                            "entry literal differs from frozen search");
            }
            for (const auto& a : o["access"])
                if (a["slice"].contains("indices_ref"))
                    check_ref(a["slice"]["indices_ref"], "ADDRESS");
            for (const auto& d : o["deps"]) {
                uint64_t producer = d["producer_op"];
                related = producer;
                auto previous = std::find_if(
                    j["operations"].begin(), j["operations"].end(), [&](const Json& x) {
                        return x["op_id"] == producer;
                    });
                require(producer < op.op_id && previous != j["operations"].end(),
                        "/deps",
                        "missing, self or future producer");
                require((*previous)["scope"]["query"] == o["scope"]["query"] &&
                            (*previous)["scope"]["query_ordinal"] == o["scope"]["query_ordinal"],
                        "/deps",
                        "cross-query dependency");
                auto port = std::find_if((*previous)["outputs"].begin(),
                                         (*previous)["outputs"].end(),
                                         [&](const Json& x) {
                                             return x["name"] == d["port"];
                                         });
                require(
                    port != (*previous)["outputs"].end(), "/deps/port", "dependency port missing");
                if (d.contains("state_version"))
                    require((*port)["type"]["name"] == "query_state" && d["kind"] == "ORDER",
                            "/deps/state_version",
                            "state version only valid on ordered state output");
            }
            if (!o["access"].empty()) {
                const auto& a = o["access"][0]["slice"];
                for (const auto& i : o["inputs"])
                    if (i["port"] == "ids") {
                        const auto& v = i["value"];
                        if (v["ref"] == "op_output")
                            require(a.contains("indices_ref") && a["indices_ref"] == v["output"],
                                    "/access",
                                    "access address and ids input differ");
                        else if (v["ref"] == "literal")
                            require(a.contains("indices") && a["indices"] == v["value"],
                                    "/access",
                                    "literal address and access differ");
                        else if (v["ref"] == "object_slice")
                            require(a == v["slice"], "/access", "slice input and access differ");
                        else
                            fail("/inputs/ids", "invalid address input");
                    }
            }
            doc.operations.push_back(std::move(op));
        } catch (Error& e) {
            e.query = o["scope"]["query"];
            e.op = o["op_id"];
            e.producer = related;
            throw;
        }
    }
    return doc;
}
} // namespace ndpsim::ir
