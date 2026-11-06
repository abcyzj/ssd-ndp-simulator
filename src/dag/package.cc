#include "dag/package.h"
#include "serialization/json_schema.h"
#include <algorithm>
#include <array>
#include <fstream>
#include <limits>
#include <set>
#include <sqlite3.h>
#include <tuple>
namespace ndpsim::dag {
namespace {
using Json = serialization::Json;
void need(bool ok, const std::string &message) {
    if (!ok)
        throw std::runtime_error("DAG package: " + message);
}
uint64_t add(uint64_t a, uint64_t b) {
    need(b <= UINT64_MAX - a, "integer overflow");
    return a + b;
}
uint64_t u(const Json &j) {
    need(j.is_number_unsigned() || (j.is_number_integer() && j.get<int64_t>() >= 0),
         "uint64 required");
    return j.get<uint64_t>();
}
std::string str(const Json &j) {
    auto s = j.get<std::string>();
    need(!s.empty(), "empty string");
    return s;
}
Json parse(const std::string &raw) {
    size_t depth = 0;
    bool quote = false, escape = false;
    for (char c : raw) {
        if (quote) {
            if (escape)
                escape = false;
            else if (c == '\\')
                escape = true;
            else if (c == '"')
                quote = false;
        } else if (c == '"')
            quote = true;
        else if (c == '{' || c == '[')
            need(++depth <= 64, "JSON nesting budget");
        else if (c == '}' || c == ']') {
            need(depth > 0, "JSON nesting");
            --depth;
        }
    }
    return serialization::parse_unique(
        raw, [](const std::string &) { throw std::runtime_error("duplicate JSON key"); });
}
std::string canonical(const Json &j) { return j.dump(-1, ' ', true) + "\n"; }
std::string read(const std::filesystem::path &p, uint64_t limit) {
    need(std::filesystem::is_regular_file(p) && !std::filesystem::is_symlink(p), "file/symlink");
    auto n = std::filesystem::file_size(p);
    need(n <= limit, "metadata budget");
    std::ifstream f(p, std::ios::binary);
    std::string raw(n, '\0');
    f.read(raw.data(), raw.size());
    need(bool(f), "file read");
    return raw;
}
std::string hash(const std::filesystem::path &p, uint64_t limit) {
    need(std::filesystem::is_regular_file(p) && !std::filesystem::is_symlink(p),
         "hash file/symlink");
    need(std::filesystem::file_size(p) <= limit, "file byte budget");
    std::ifstream f(p, std::ios::binary);
    capture::Sha256 h;
    std::array<char, 65536> b;
    while (f) {
        f.read(b.data(), b.size());
        h.update(std::string_view(b.data(), f.gcount()));
    }
    need(f.eof(), "hash read");
    return h.hexdigest();
}
capture::Key key(const Json &j) { return {u(j.at("query")), str(j.at("op")), str(j.at("stage"))}; }
using LocalKey = std::pair<std::string, std::string>;
LocalKey local(const capture::Key &k) { return {k.op, k.stage}; }
std::vector<capture::Dependency> deps(const Json &j) {
    std::vector<capture::Dependency> out;
    for (const auto &d : j) {
        auto s = str(d.at("kind"));
        capture::EdgeKind kind;
        if (s == "DATA")
            kind = capture::EdgeKind::Data;
        else if (s == "ADDRESS")
            kind = capture::EdgeKind::Address;
        else if (s == "CONTROL")
            kind = capture::EdgeKind::Control;
        else {
            need(s == "ORDER", "unknown edge");
            kind = capture::EdgeKind::Order;
        }
        out.push_back({kind, key(d.at("source").at("key")), str(d.at("source").at("port")),
                       str(d.at("input"))});
    }
    return out;
}
capture::Counters counters(const Json &j) {
    capture::Counters c;
    for (auto it = j.begin(); it != j.end(); ++it)
        c.emplace(it.key(), std::make_pair(u(it.value().at("value")), str(it.value().at("unit"))));
    return c;
}
struct Statement {
    sqlite3_stmt *p = nullptr;
    Statement(sqlite3 *db, const char *sql) {
        need(sqlite3_prepare_v2(db, sql, -1, &p, nullptr) == SQLITE_OK, sqlite3_errmsg(db));
    }
    ~Statement() { sqlite3_finalize(p); }
    void text(int i, const std::string &s) {
        need(sqlite3_bind_text(p, i, s.data(), s.size(), SQLITE_TRANSIENT) == SQLITE_OK,
             "SQL bind");
    }
    void integer(int i, uint64_t n) {
        need(n <= uint64_t(INT64_MAX), "SQL integer range");
        need(sqlite3_bind_int64(p, i, n) == SQLITE_OK, "SQL bind");
    }
    int step() {
        int r = sqlite3_step(p);
        need(r == SQLITE_ROW || r == SQLITE_DONE, sqlite3_errmsg(sqlite3_db_handle(p)));
        return r;
    }
    uint64_t integer(int i) {
        auto n = sqlite3_column_int64(p, i);
        need(n >= 0, "negative SQL value");
        return n;
    }
    std::string text(int i) { return reinterpret_cast<const char *>(sqlite3_column_text(p, i)); }
};
} // namespace
struct Package::Data {
    std::filesystem::path root;
    Limits limits;
    sqlite3 *db = nullptr;
    std::ifstream index, operations;
    std::vector<Query> queries;
    std::map<uint64_t, std::set<LocalKey>> terminals;
    uint64_t detail_reads = 0, peak_detail = 0;
    ~Data() {
        if (db)
            sqlite3_close(db);
    }
    void sql(const std::string &s) {
        char *error = nullptr;
        int rc = sqlite3_exec(db, s.c_str(), nullptr, nullptr, &error);
        std::string message = error ? error : "SQL";
        sqlite3_free(error);
        need(rc == SQLITE_OK, message);
    }
    uint64_t disk() {
        Statement q(db, "PRAGMA page_count");
        q.step();
        return q.integer(0) * 4096;
    }
    std::string at(std::ifstream &f, uint64_t offset, uint64_t length) {
        need(length > 0 && length <= limits.line_bytes && offset <= uint64_t(INT64_MAX),
             "record bounds");
        f.clear();
        f.seekg(offset);
        std::string raw(length, '\0');
        f.read(raw.data(), raw.size());
        need(bool(f) && raw.back() == '\n', "indexed/truncated record");
        return raw;
    }
};
Package::Package(const std::filesystem::path &root, Receipt receipt,
                 const std::filesystem::path &scratch, Limits limits)
    : data_(std::make_unique<Data>()) {
    auto &d = *data_;
    d.root = root;
    d.limits = limits;
    need(limits.line_bytes > 0 && limits.line_bytes <= 16 * 1024 * 1024 &&
             limits.query_index_bytes > 0 && limits.query_index_bytes <= 16 * 1024 * 1024 &&
             limits.query_stages > 0 && limits.scratch_bytes >= 4096 && limits.total_bytes > 0,
         "deployment limits");
    need(std::filesystem::is_directory(root) && !std::filesystem::is_symlink(root),
         "package directory");
    std::set<std::string> files;
    uint64_t total = 0;
    for (const auto &e : std::filesystem::directory_iterator(root)) {
        files.insert(e.path().filename().string());
        need(e.is_regular_file() && !e.is_symlink(), "package file");
        total = add(total, e.file_size());
    }
    need(files == std::set<std::string>{"manifest.json", "index.jsonl", "operations.jsonl",
                                        "tail.json"} &&
             total <= limits.total_bytes,
         "file coverage/total budget");
    auto manifest_raw = read(root / "manifest.json", limits.line_bytes),
         tail_raw = read(root / "tail.json", limits.line_bytes);
    auto m = parse(manifest_raw), t = parse(tail_raw);
    need(m.at("format") == "ndpsim-static-ir/2" && t.at("format") == "ndpsim-static-ir-tail/2",
         "format");
    auto ml = m.at("limits");
    need(u(ml.at("line_bytes")) <= limits.line_bytes &&
             u(ml.at("query_index_bytes")) <= limits.query_index_bytes &&
             u(ml.at("active_stages")) <= limits.query_stages,
         "package exceeds deployment limits");
    d.limits.line_bytes = u(ml.at("line_bytes"));
    d.limits.query_index_bytes = u(ml.at("query_index_bytes"));
    d.limits.query_stages = u(ml.at("active_stages"));
    need(d.limits.line_bytes && d.limits.query_index_bytes && d.limits.query_stages,
         "zero package limit");
    std::map<std::string, std::string> hashes;
    for (const auto &f : m.at("files")) {
        auto name = str(f.at("path"));
        need((name == "index.jsonl" || name == "operations.jsonl") && !hashes.count(name),
             "package file mapping");
        need(std::filesystem::file_size(root / name) == u(f.at("bytes")), "file size");
        auto h = hash(root / name, limits.total_bytes);
        need(h == str(f.at("sha256")), "file hash");
        hashes[name] = h;
    }
    need(hashes.size() == 2, "missing package files");
    std::string identity = "ndpsim-static-ir/2";
    identity += '\0';
    for (const auto &h :
         {capture::sha256(manifest_raw), hashes.at("index.jsonl"), hashes.at("operations.jsonl")})
        for (size_t i = 0; i < h.size(); i += 2)
            identity += char(std::stoul(h.substr(i, 2), nullptr, 16));
    auto package_id = capture::sha256(identity);
    // Tail is excluded from package_id to avoid a hash cycle. Its complete
    // contents are derivable here, so compare types and fields as well as values.
    Json wanted_tail = {{"format", "ndpsim-static-ir-tail/2"}, {"complete", true},
                        {"lost_records", uint64_t(0)},         {"counts", m.at("counts")},
                        {"terminals", m.at("terminals")},      {"package_sha256", package_id}};
    need(receipt.package_sha256 == package_id && canonical(t) == canonical(wanted_tail),
         "package/tail binding");
    auto report_raw = read(receipt.report, limits.line_bytes);
    need(capture::sha256(report_raw) == receipt.report_sha256, "report hash");
    auto report = parse(report_raw);
    need(report.at("format") == "ndpsim-package-validation/1" &&
             report.at("structural_passed") == true && report.at("package_sha256") == package_id &&
             report.at("registry_sha256") == receipt.registry_sha256 &&
             report.at("counts") == m.at("counts") &&
             report.at("source") == m.at("provenance").at("kind") &&
             m.at("provenance").at("extension_sha256") == receipt.registry_sha256,
         "structural receipt binding");
    if (m.at("provenance").at("kind") == "synthetic-fixture")
        need(receipt.allow_fixture, "fixture requires explicit opt-in");
    else {
        need(m.at("provenance").at("kind") == "native-capture", "unknown provenance");
        auto raw = read(receipt.semantic_report, limits.line_bytes);
        need(capture::sha256(raw) == receipt.semantic_sha256, "semantic hash");
        auto r = parse(raw);
        need(r.at("independent_semantic_passed") == true && r.at("structural_passed") == true &&
                 r.at("package_sha256") == package_id && r.at("provenance") == m.at("provenance") &&
                 r.at("query_ids") == m.at("provenance").at("query_ids"),
             "independent semantic receipt");
    }
    for (const auto &q : m.at("provenance").at("query_ids")) {
        auto id = u(q);
        need(!d.terminals.count(id), "duplicate query");
        d.terminals[id] = {};
        d.queries.push_back({id, 0, 0});
    }
    need(!d.queries.empty(), "empty package");
    for (const auto &v : m.at("terminals")) {
        auto k = key(v);
        need(d.terminals.count(k.query) && d.terminals[k.query].insert(local(k)).second,
             "terminal query/duplicate");
    }
    need(!std::filesystem::exists(scratch) && !std::filesystem::is_symlink(scratch),
         "scratch must be new");
    need(sqlite3_open_v2(scratch.string().c_str(), &d.db,
                         SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_EXCLUSIVE,
                         nullptr) == SQLITE_OK,
         "scratch open");
    d.sql("PRAGMA page_size=4096;PRAGMA journal_mode=OFF;PRAGMA synchronous=OFF;PRAGMA "
          "temp_store=FILE;PRAGMA cache_size=-2048;");
    d.sql("PRAGMA max_page_count=" + std::to_string(limits.scratch_bytes / 4096));
    d.sql("CREATE TABLE nodes(q TEXT,op TEXT,st TEXT,rank INTEGER,pos INTEGER,len INTEGER,PRIMARY "
          "KEY(q,op,st),UNIQUE(q,rank));BEGIN");
    d.index.open(root / "index.jsonl", std::ios::binary);
    d.operations.open(root / "operations.jsonl", std::ios::binary);
    need(bool(d.index) && bool(d.operations), "stream open");
    std::map<uint64_t, size_t> ids;
    for (size_t i = 0; i < d.queries.size(); ++i)
        ids[d.queries[i].id] = i;
    uint64_t offset = 0, edges = 0;
    while (d.index.peek() != EOF) {
        std::string raw;
        char c;
        while (d.index.get(c)) {
            raw += c;
            need(raw.size() <= d.limits.line_bytes, "index line budget");
            if (c == '\n')
                break;
        }
        need(!raw.empty() && raw.back() == '\n', "index truncation");
        auto j = parse(raw);
        auto k = key(j.at("key"));
        need(ids.count(k.query), "index query");
        auto &q = d.queries[ids.at(k.query)];
        q.stages = add(q.stages, 1);
        q.index_bytes = add(q.index_bytes, canonical(j).size());
        need(q.stages <= d.limits.query_stages && q.index_bytes <= d.limits.query_index_bytes,
             "query index budget");
        Statement insert(d.db, "INSERT INTO nodes VALUES(?,?,?,?,?,?)");
        insert.text(1, std::to_string(k.query));
        insert.text(2, k.op);
        insert.text(3, k.stage);
        insert.integer(4, u(j.at("topological_rank")));
        insert.integer(5, offset);
        insert.integer(6, raw.size());
        insert.step();
        edges = add(edges, j.at("dependencies").size());
        offset = add(offset, raw.size());
    }
    d.sql("COMMIT");
    uint64_t stages = 0;
    for (const auto &q : d.queries) {
        need(q.stages && !d.terminals.at(q.id).empty(), "query/terminal coverage");
        stages = add(stages, q.stages);
    }
    need(stages == u(m.at("counts").at("stages")) &&
             edges == u(m.at("counts").at("dependencies")) &&
             d.queries.size() == u(m.at("counts").at("queries")),
         "index counts");
    need(hash(root / "index.jsonl", limits.total_bytes) == hashes.at("index.jsonl") &&
             hash(root / "operations.jsonl", limits.total_bytes) == hashes.at("operations.jsonl"),
         "source changed during indexing");
}
Package::~Package() = default;
const std::vector<Query> &Package::queries() const { return data_->queries; }
Graph Package::graph(size_t query_index) {
    auto &d = *data_;
    Graph g;
    g.query = d.queries.at(query_index);
    Statement rows(d.db, "SELECT pos,len FROM nodes WHERE q=? ORDER BY rank");
    rows.text(1, std::to_string(g.query.id));
    while (rows.step() == SQLITE_ROW) {
        auto j = parse(d.at(d.index, rows.integer(0), rows.integer(1)));
        auto k = key(j.at("key"));
        need(k.query == g.query.id && j.at("file") == "operations.jsonl", "indexed query/file");
        Node n{k,
               u(j.at("topological_rank")),
               u(j.at("offset_bytes")),
               u(j.at("length_bytes")),
               str(j.at("stage_sha256")),
               deps(j.at("dependencies")),
               d.terminals.at(k.query).count(local(k)) != 0};
        need(n.rank == g.nodes.size() && n.length <= d.limits.line_bytes,
             "contiguous ranks/line budget");
        g.nodes.push_back(std::move(n));
    }
    need(g.nodes.size() == g.query.stages, "query index coverage");
    std::map<LocalKey, size_t> bykey;
    for (size_t i = 0; i < g.nodes.size(); ++i)
        need(bykey.emplace(local(g.nodes[i].key), i).second, "duplicate node");
    g.successors.resize(g.nodes.size());
    for (size_t i = 0; i < g.nodes.size(); ++i) {
        std::set<std::tuple<LocalKey, std::string, std::string>> seen;
        for (const auto &e : g.nodes[i].dependencies) {
            need(e.source.query == g.query.id && bykey.count(local(e.source)),
                 "missing/cross-query source");
            auto source = bykey.at(local(e.source));
            need(source < i, "cycle/invalid rank");
            need(seen.emplace(local(e.source), e.source_port, e.input).second, "duplicate edge");
            g.successors[source].push_back(i);
        }
    }
    for (size_t i = 0; i < g.nodes.size(); ++i)
        need(g.nodes[i].terminal == g.successors[i].empty(), "terminal closure");
    return g;
}
Stage Package::stage(const Node &n) {
    auto &d = *data_;
    auto raw = d.at(d.operations, n.offset, n.length);
    ++d.detail_reads;
    d.peak_detail = std::max<uint64_t>(d.peak_detail, raw.size());
    auto op = parse(raw);
    need(u(op.at("query")) == n.key.query && op.at("id") == n.key.op, "indexed operation");
    const Json *selected = nullptr;
    for (const auto &s : op.at("stages"))
        if (s.at("id") == n.key.stage) {
            need(!selected, "duplicate stage");
            selected = &s;
        }
    need(selected && capture::sha256(canonical(*selected)) == n.stage_sha256,
         "stage digest/missing");
    const auto &s = *selected;
    need(u(s.at("topological_rank")) == n.rank, "stage rank");
    auto actual = deps(s.at("dependencies"));
    need(actual.size() == n.dependencies.size(), "stage/index dependencies");
    for (size_t i = 0; i < actual.size(); ++i) {
        const auto &a = actual[i];
        const auto &b = n.dependencies[i];
        need(a.kind == b.kind && a.source.query == b.source.query &&
                 local(a.source) == local(b.source) && a.source_port == b.source_port &&
                 a.input == b.input,
             "stage/index dependency mismatch");
    }
    Stage out;
    out.key = n.key;
    out.operator_id = str(op.at("operator"));
    out.operator_version = str(op.at("operator_version"));
    auto &m = out.metadata;
    m.id = n.key.stage;
    m.kind = str(s.at("kind"));
    m.topological_rank = n.rank;
    m.dependencies = actual;
    m.work = counters(s.at("work"));
    m.features = counters(s.at("features"));
    m.context = str(s.at("context"));
    m.segment = u(s.at("segment"));
    m.decomposition_rule = str(s.at("decomposition_rule"));
    if (!s.at("chunk").is_null())
        m.chunk = u(s.at("chunk"));
    for (const auto &o : s.at("outputs"))
        m.outputs.push_back({str(o.at("port")), str(o.at("type")), u(o.at("logical_bytes")),
                             u(o.at("version")), str(o.at("digest"))});
    for (const auto &a : s.at("accesses"))
        m.accesses.push_back({str(a.at("object")), u(a.at("version")), u(a.at("offset_bytes")),
                              u(a.at("length_bytes"))});
    for (const auto &v : s.at("state_versions")) {
        capture::StateVersion state{
            str(v.at("name")), u(v.at("before")), u(v.at("after")), {}, str(v.at("output"))};
        if (!v.at("input").is_null())
            state.input = str(v.at("input"));
        m.state_versions.push_back(std::move(state));
    }
    return out;
}
uint64_t Package::detail_reads() const { return data_->detail_reads; }
uint64_t Package::peak_detail_bytes() const { return data_->peak_detail; }
uint64_t Package::scratch_bytes() const { return data_->disk(); }
} // namespace ndpsim::dag
