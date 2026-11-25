#include "run.h"
#include "build_identity.h"
#include "backend/observation.h"
#include "backend/ordinary_device.h"
#include "dag/executor.h"
#include "model/cost/cost_model.h"
#include "ssd/NVM_PHY_ONFI.h"
#include "ssd/FTL.h"
#include "ssd/TSU_Priority_OutOfOrder.h"
#include "timing/local_reads.h"
#include "timing/messages.h"

#include <array>
#include <cmath>
#include <deque>
#include <fstream>
#include <list>
#include <unordered_map>
#include <nlohmann/json.hpp>

namespace maxsim {
namespace {
using namespace ndpsim;
using J = nlohmann::json;
using timing::checked_add;
using timing::Done;
using timing::MessageKind;
namespace fs = std::filesystem;

void require(bool condition, const char* detail) {
    if (!condition)
        throw std::invalid_argument(detail);
}

std::string read(const fs::path& path, uint64_t limit = 16 * 1024 * 1024) {
    require(fs::is_regular_file(path) && fs::file_size(path) <= limit,
            "missing execution metadata or size limit exceeded");
    std::ifstream input(path, std::ios::binary);
    require(bool(input), "cannot open execution metadata");
    return {std::istreambuf_iterator<char>(input), {}};
}

uint64_t integer(const J& value) {
    require(value.is_number_unsigned(), "expected unsigned integer");
    return value.get<uint64_t>();
}

uint64_t integral_scale(const J& value, unsigned scale) {
    const double scaled = value.get<double>() * scale;
    require(std::isfinite(scaled) && scaled >= 0 && scaled < 0x1p64 &&
                std::floor(scaled) == scaled,
            "E0 compute parameters require integer ns and FLOP/ns");
    return static_cast<uint64_t>(scaled);
}

uint64_t ceil_div(uint64_t value, uint64_t unit) {
    return value / unit + (value % unit != 0);
}

constexpr const char* batch_operator = "maxsim.score-batch";
constexpr const char* batch_context = "maxsim.e0-ready-batch/1";
constexpr const char* protocol = "maxsim-offload/1";
constexpr uint64_t score_bytes = 12;
} // namespace

struct Run::State {
    struct Document {
        uint64_t id = 0, credit = 0, reads_left = 0, moves_left = 0, flop = 0;
        size_t submitted = 0;
        bool controller = false, hit = false, fill = false;
        std::vector<layout::Plan> plans;
        capture::Key key;
        Done read_done;
    };
    struct Query {
        std::array<size_t, 2> next_document{};
        std::array<std::vector<size_t>, 2> routes;
        size_t controller_candidates = 0;
        bool command_arrived = false;
        uint64_t window = 0, controller_input = 0, controller_scores = 0, host_scores = 0;
        std::array<std::map<std::pair<uint64_t, size_t>, Done>, 2> ready;
        std::map<size_t, Done> awaiting_return;
    };
    struct Side {
        timing::FifoResource scoring;
        std::unique_ptr<timing::BufferModel> buffer;
        size_t next_score_query = 0;
        uint64_t outstanding = 0, scored = 0, batches = 0, compute_ns = 0;
    };
    struct OrdinaryRead {
        size_t candidate, slice;
    };
    enum class FillPath { None, Ordinary, Staged };
    enum class Admission { All, Second, Frequency };
    struct Backfill {
        FillPath path = FillPath::None;
        Admission policy = Admission::All;
        uint64_t history_limit = 0, window_ns = 0, budget_bytes = 0;
        uint64_t epoch = 0, used = 0, admitted = 0, deduplicated = 0;
        uint64_t completed = 0, published = 0, vector_bytes = 0;
        uint64_t ordinary_documents = 0, last_completion = 0;
        std::deque<uint64_t> history;
        std::unordered_map<uint64_t, uint64_t> frequency;
        std::unordered_map<uint64_t, size_t> inflight;
        std::vector<size_t> ready;
    } fill;
    struct LinkLedger {
        uint64_t messages = 0, payload = 0, wire = 0, service = 0;
    };

    EventBridge& bridge;
    EventBridge::Clock now;
    timing::Runtime runtime{bridge, now};
    timing::HostCpu host{bridge, now, 1};
    timing::FifoResource staging{bridge, now, 1}, command_service{bridge, now, 1};
    std::array<Side, 2> sides{{{timing::FifoResource(bridge, now, 1), nullptr},
                               {timing::FifoResource(bridge, now, 1), nullptr}}};
    timing::MessageRules rules;
    std::unique_ptr<dag::Package> package;
    std::unique_ptr<layout::Layout> layout;
    std::unique_ptr<DeviceConfig> config;
    std::unique_ptr<OrdinaryDevice> device;
    std::unique_ptr<timing::MetadataLocalReads> local;
    std::unique_ptr<timing::MetadataPcie> pcie;
    std::unique_ptr<dag::Executor> executor;
    dag::Config scheduling;
    std::optional<cost::Profile> cost_profile;
    cost::Target target;
    std::ofstream trace;
    Observation observation{false};
    std::vector<Document> documents;
    std::map<uint64_t, OrdinaryRead> ordinary;
    std::vector<Query> queries;
    std::list<uint64_t> cache_order;
    std::unordered_map<uint64_t, std::list<uint64_t>::iterator> cached;
    size_t cache_pages = 0, count = 0, concurrency = 1;
    size_t next_read_query = 0;
    uint64_t hits = 0;
    bool experiment = false, detailed = true, open_loop = false, overlap = true;
    std::string last_block;
    uint64_t blocked_since = 0;
    std::map<std::string, uint64_t> blocked_ns;
    std::map<uint64_t, size_t> active_queries;
    std::vector<bool> scored;
    std::string placement;
    bool offload = false, hybrid = false, free = false, launched = false, finished = false, reported = false;
    bool reserving = false, local_read_die_admission = false, nvme_read_die_admission = false;
    SSD_Components::TSU_Priority_OutOfOrder* die_admission_tsu = nullptr;
    uint64_t origin = 0, visible = 0, preload = 0;
    uint64_t window_limit = 0, qd_limit = 0, buffer_limit = 0, batch_limit = 0, port_rate = 0;
    uint64_t command_ns = 0, command_header = 0, result_header = 0, document_bytes = 0;
    uint64_t query_vector_bytes = 0, document_id_bytes = 0, downlink = 0, uplink = 0, topk_bytes = 0, query_flop = 0, expected_reads = 0;
    uint64_t qd = 0, window = 0, peak_qd = 0, peak_window = 0, next_document = 0;
    uint64_t submitted_reads = 0, completed_reads = 0, read_bytes = 0, effective_bytes = 0;
    uint64_t local_reads = 0, ordinary_bytes = 0, command_payload = 0, result_payload = 0;
    uint64_t dma = 0, staged_bytes = 0, batches = 0, flops = 0, compute_ns = 0;
    uint64_t controller_input = 0, controller_scores = 0, host_scores = 0;
    uint64_t peak_controller_input = 0, peak_controller_scores = 0, peak_host_scores = 0;
    uint64_t topk_count = 0, protocol_messages = 0, peak_scoring_engines = 0;
    uint64_t nand_read_bytes = 0, nand_user_read_bytes = 0, nand_reads = 0;
    std::map<std::string, LinkLedger> link_ledger;
    static State* active;

    State(const fs::path&, EventBridge&, EventBridge::Clock);
    ~State() {
        if (active == this)
            active = nullptr;
    }

    void emit(const J& record) {
        trace << record.dump() << '\n';
        require(bool(trace), "cannot write MaxSim trace");
    }
    void install_cost(const J& compute, uint64_t flop);
    uint64_t demand(uint64_t flop, bool controller) const;
    size_t offloaded(const Query&) const;
    uint64_t command_bytes(size_t candidates) const;
    void route(size_t query);
    void configure_backfill(const J&, uint64_t corpus_documents);
    void observe_access(uint64_t id);
    uint64_t access_frequency(uint64_t id) const;
    const char* admit_backfill(size_t index);
    void complete_backfill(size_t index);
    void pump_backfill();
    void send_command(size_t query, Done done = {});
    void connect();
    void start_executor();
    void read_plan(const dag::Stage&, const J& candidate);
    timing::Operation lower(const dag::Stage&);
    void poll();
    void pump_reads();
    void complete_read(size_t index, size_t slice);
    void moved(size_t index);
    void pump_scores(bool controller);
    void send_result(size_t query);
    void occupancy(size_t query);
    void touch(uint64_t id);
    void blocked(const char* reason, size_t index);
    void verify() const;
    void report();

    static void nand_completed(SSD_Components::NVM_Transaction_Flash* tr) {
        using namespace SSD_Components;
        if (!active || !active->launched || tr->Type != Transaction_Type::READ)
            return;
        auto& run = *active;
        const auto bytes = tr->Data_and_metadata_size_in_byte;
        const bool user = tr->Source == Transaction_Source_Type::USERIO;
        run.nand_read_bytes += bytes;
        run.nand_user_read_bytes += user ? bytes : 0;
        ++run.nand_reads;
        if (run.detailed) run.emit({{"type", "nand_read"}, {"time_ns", run.now()}, {"bytes", bytes},
                  {"source", int(tr->Source)}, {"user_data", user},
                  {"channel", tr->Address.ChannelID}, {"lpa", tr->LPA},
                  {"transfer_ns", tr->STAT_transfer_time},
                  {"execution_ns", tr->STAT_execution_time}});
    }
};

Run::State* Run::State::active = nullptr;

Run::State::State(const fs::path& path, EventBridge& b, EventBridge::Clock clock)
    : bridge(b), now(std::move(clock)) {
    const auto run = J::parse(read(path));
    require(run.at("format") == "ndpsim-maxsim-run/1" &&
                run.size() == 8 + size_t(run.contains("device_limits")) + size_t(run.contains("preflight_only"))
                    + size_t(run.contains("closed_loop")) + size_t(run.contains("open_loop"))
                    + size_t(run.contains("local_read_die_admission"))
                    + size_t(run.contains("nvme_read_die_admission"))
                    + size_t(run.contains("cache_snapshot"))
                    + size_t(run.contains("backfill")) + size_t(run.contains("detailed_trace")),
            "MaxSim run format/fields");
    const bool preflight_only = run.value("preflight_only", false);
    DeviceLimits limits;
    if (run.contains("device_limits")) {
        const auto& l = run.at("device_limits");
        require(l.is_object() && l.size() == 3, "MaxSim DeviceLimits fields");
        limits = {integer(l.at("xml_bytes")), integer(l.at("metadata_bytes")), integer(l.at("preload_pages"))};
        require(limits.xml_bytes && limits.metadata_bytes && limits.preload_pages, "MaxSim DeviceLimits must be positive");
    }
    placement = run.at("placement");
    offload = placement == "device_all";
    if (placement == "hybrid_backfill_a")
        fill.path = FillPath::Ordinary;
    if (placement == "hybrid_backfill_b")
        fill.path = FillPath::Staged;
    hybrid = placement == "hybrid_miss_only" || fill.path != FillPath::None;
    require(run.contains("backfill") == (fill.path != FillPath::None), "MaxSim backfill configuration required for A/B");
    free = placement == "host_compute_free";
    local_read_die_admission = run.value("local_read_die_admission", false);
    nvme_read_die_admission = run.value("nvme_read_die_admission", false);
    require(!local_read_die_admission || offload || hybrid,
            "local read die admission requires a controller placement");
    require(!nvme_read_die_admission || (!offload && !hybrid) || fill.path == FillPath::Ordinary,
            "NVMe read die admission requires an ordinary-read placement");
    require(offload || hybrid || free || placement == "host", "unsupported MaxSim placement");
    const auto directory = fs::absolute(path).parent_path();
    const fs::path bundle = directory / run.at("bundle").get<std::string>();
    const auto output = directory / run.at("output").get<std::string>();
    const auto receipt_raw = read(bundle / "receipt.json");
    require(capture::sha256(receipt_raw) == run.at("receipt_sha256"), "generation receipt identity");
    const auto receipt = J::parse(receipt_raw);
    require(receipt.at("format") == "ndpsim-maxsim-generation/1" && receipt.at("passed") == true &&
                receipt.at("exit_code") == 0 && receipt.at("source") == "synthetic-fixture",
            "successful MaxSim fixture receipt required");
    const auto& hashes = receipt.at("artifacts");
    auto bound = [&](const char* name) {
        auto raw = read(bundle / name);
        require(capture::sha256(raw) == hashes.at(name), "bundle artifact hash mismatch");
        return J::parse(raw);
    };
    auto effective = bound("effective-config.json");
    const auto audit = bound("accounting.json");
    const auto structure = bound("structure.json");
    const auto registry = bound("operators.json");
    const auto ledger_raw = read(bundle / "logical-reads.jsonl", 128 * 1024 * 1024);
    require(capture::sha256(ledger_raw) == hashes.at("logical-reads.jsonl"), "logical read ledger hash mismatch");
    std::istringstream ledger(ledger_raw);
    const auto manifest = bound("package/manifest.json");
    const auto physical = bound("layout.json");
    require(registry.at("format") == "ndpsim-operator-registry/1" &&
                hashes.at("operators.json") == structure.at("registry_sha256") &&
                manifest.at("provenance").at("kind") == "synthetic-fixture" &&
                manifest.at("provenance").at("workload") == "maxsim" &&
                manifest.at("provenance").at("semantic_version") == "full-maxsim/1",
            "unsupported MaxSim IR");
    dag::Receipt ir;
    ir.report = bundle / "structure.json";
    ir.report_sha256 = hashes.at("structure.json");
    ir.package_sha256 = receipt.at("package_sha256");
    ir.registry_sha256 = structure.at("registry_sha256");
    ir.allow_fixture = true;
    package = std::make_unique<dag::Package>(bundle / "package", ir,
        directory / run.at("scratch").get<std::string>());
    auto& c = effective.at("config");
    const auto total_queries = integer(c.at("generation").at("queries"));
    require(total_queries && package->queries().size() == total_queries, "MaxSim query coverage");
    open_loop = run.contains("open_loop");
    require(!open_loop || !run.contains("closed_loop"), "MaxSim load modes are exclusive");
    const char* mode = open_loop ? "open_loop" : "closed_loop";
    const auto* initial_cache = run.contains("cache_snapshot") ? &run.at("cache_snapshot") : nullptr;
    experiment = run.contains(mode);
    detailed = run.value("detailed_trace", !experiment);
    if (experiment) {
        require(!initial_cache, "standalone cache snapshot and experiment settings are exclusive");
        const auto& experiment = run.at(mode);
        require(experiment.size() == (open_loop ? 5 : 3), "MaxSim experiment configuration fields");
        concurrency = integer(experiment.at("concurrency"));
        if (open_loop) {
            const auto& offsets = experiment.at("offered_offsets_ns");
            const auto qps = experiment.at("qps").get<double>();
            require(offsets.is_array() && offsets.size() == total_queries && std::isfinite(qps) && qps > 0,
                    "MaxSim open-loop arrival configuration");
            for (const auto& offset : offsets)
                scheduling.offered_ns.push_back(integer(offset));
            require(std::is_sorted(scheduling.offered_ns.begin(), scheduling.offered_ns.end()),
                    "MaxSim arrivals must be in query order");
            c.at("load").at("mode") = mode;
            c.at("load").at("open_loop_qps") = qps;
            c.at("load")["admission_limit"] = concurrency;
        } else {
            c.at("load").at("closed_loop_concurrency") = concurrency;
        }
        initial_cache = &experiment;
    } else {
        require(total_queries == 1 && c.at("load").at("closed_loop_concurrency") == 1 &&
                    c.at("cache").at("initial_state") == "cold" &&
                    c.at("cache").at("host_capacity_bytes") == 0,
                "MaxSim E0 requires one query and zero cache capacity");
    }
    require(!initial_cache || experiment || !offload, "standalone cache snapshot requires a host cache");
    if (initial_cache) {
        require(experiment || (initial_cache->is_object() && initial_cache->size() == 2),
                "MaxSim cache snapshot fields");
        c.at("cache").at("host_capacity_bytes") = initial_cache->at("host_capacity_bytes");
        c.at("cache").at("host_capacity_fraction") = nullptr;
        if (!initial_cache->at("cache_oldest_first").empty())
            c.at("cache").at("initial_state") = "prewarmed";
    }
    const auto& derived = effective.at("derived");
    const auto& ex = c.at("execution");
    const auto& cache = c.at("cache");
    const auto& load = c.at("load");
    const auto& h = c.at("hardware");
    const auto& platform = run.at("platform");
    require(platform.size() == 6 && platform.at("format") == "ndpsim-maxsim-e0-platform/1" &&
                integer(platform.at("host_dram_service_ns")) == 0 &&
                integer(platform.at("host_topk_service_ns")) == 0,
            "unsupported MaxSim E0 platform model");
    require(load.at("mode") == mode && concurrency && concurrency <= total_queries &&
                cache.at("device_extra_capacity_bytes") == 0 &&
                cache.at("host_fill_on_miss") == true && cache.at("cross_query_miss_coalescing") == false,
            "MaxSim requires supported load and cache settings");
    overlap = ex.at("overlap_read_compute").get<bool>();
    require(ex.at("scoring_engines_per_side") == 1 &&
                ex.at("wait_for_full_batch") == false && ex.at("cross_query_compute_batching") == false,
            "unsupported MaxSim E0 scheduling model");
    require(effective.at("format") == "ndpsim-maxsim-generation/1" &&
                c.at("compute").at("mode") == "equivalent_full_maxsim" &&
                c.at("compute").at("host_compute_free_zeros_launch") == true &&
                h.at("internal_bandwidth_mode") == "simulated" && h.at("hbf_enabled") == false &&
                (h.at("local_memory_cost_model") == "inherit" ||
                 h.at("local_memory_cost_model") == "inherit_or_explicit_upper_bound"),
            "unsupported MaxSim E0 compute/memory model");
    require(h.at("backend_commit") == mqsim_commit && run.at("mqsim_source_sha256") == mqsim_source_sha256,
            "MQSim build and input identity mismatch");

    window_limit = integer(ex.at("read_window_documents_per_query"));
    qd_limit = integer(ex.at("global_outstanding_reads"));
    buffer_limit = integer(ex.at("input_buffer_bytes_per_side"));
    batch_limit = integer(ex.at("compute_batch_documents"));
    port_rate = integer(platform.at("controller_staging_bytes_per_ns"));
    command_header = integer(platform.at("command_header_bytes"));
    result_header = integer(platform.at("result_header_bytes"));
    command_ns = integral_scale(c.at("compute").at("offload_command_us"), 1000);
    document_bytes = integer(derived.at("document_bytes"));
    expected_reads = integer(derived.at("query_all_miss_reads"));
    query_flop = integer(derived.at("query_flop"));
    document_id_bytes = integer(c.at("protocol").at("document_id_bytes"));
    query_vector_bytes = integer(derived.at("query_bytes"));
    downlink = integer(derived.at("query_downlink_bytes"));
    uplink = integer(derived.at("query_uplink_bytes"));
    topk_bytes = integer(derived.at("topk_bytes"));
    require(window_limit && qd_limit && batch_limit && batch_limit <= 16 && port_rate &&
                document_bytes && buffer_limit >= document_bytes,
            "unsupported E0 quotas or buffer cannot hold one document");

    require(audit.at("format") == "ndpsim-maxsim-metadata-audit/1" &&
                audit.at("passed") == true && audit.at("source") == "synthetic-fixture" &&
                audit.at("queries") == total_queries && audit.at("all_miss_logical_requests") == total_queries * expected_reads &&
                audit.at("all_miss_request_bytes") == total_queries * integer(derived.at("query_all_miss_read_bytes")) &&
                audit.at("document_effective_bytes") == total_queries * integer(derived.at("query_document_bytes")) &&
                audit.at("dot_product_flop") == total_queries * query_flop && audit.at("downlink_bytes_per_query") == downlink &&
                audit.at("uplink_bytes_per_query") == uplink && audit.at("topk_bytes_per_query") == topk_bytes,
            "MaxSim audit/derived accounting disagreement");
    const auto& objects = manifest.at("objects");
    require(objects.size() == 1, "MaxSim requires one corpus object");
    const auto& object = objects.at(0);
    layout = std::make_unique<layout::Layout>(bundle / "layout.json", fs::absolute(path),
        std::vector<capture::Object>{{object.at("id"), object.at("version"), object.at("type"),
                                      object.at("size_bytes"), object.at("shape"),
                                      object.at("content_sha256")}});
    config = std::make_unique<DeviceConfig>(h.at("mqsim_config_path").get<std::string>(), fs::absolute(path), limits);
    const auto& g = config->geometry();
    require(config->source_sha256() == h.at("mqsim_config_hash") &&
                h.at("external_effective_GBps") == g.lanes * g.lane_bandwidth,
            "MQSim XML identity or configured PCIe rate mismatch");
    require(physical.at("namespace").at("capacity_sectors") == c.at("layout").at("namespace_capacity_sectors") &&
                physical.at("namespace").at("page_bytes") == c.at("layout").at("namespace_page_bytes") &&
                layout->mapped_end_sectors() == derived.at("minimum_capacity_sectors"),
            "layout namespace or mapped range disagreement");
    require(qd_limit <= g.queue_depth, "logical read QD exceeds device queue depth");
    preload = ceil_div(layout->mapped_end_sectors(), g.page_sectors) * g.page_sectors;
    config->validate_layout(*layout, preload);

    count = integer(c.at("workload").at("candidate_count"));
    require(count && count * total_queries == audit.at("candidates"), "MaxSim candidate coverage");
    queries.resize(total_queries);
    documents.resize(count * total_queries);
    scored.resize(documents.size());
    uint64_t checked_requests = 0, checked_flop = 0;
    std::string line;
    for (size_t query = 0; query < total_queries; ++query) {
        require(package->queries().at(query).id == query && bool(std::getline(ledger, line)),
                "MaxSim query identity or missing ledger row");
        const auto row = J::parse(line);
        require(row.at("query") == query && row.at("protocol").at("downlink_bytes") == downlink &&
                    row.at("protocol").at("uplink_bytes") == uplink, "protocol ledger mismatch");
        const auto graph = package->graph(query);
        require(count == row.at("documents").size() && graph.nodes.size() == 2 * count + 2,
                "candidate/stage coverage mismatch");
        for (const auto& node : graph.nodes) {
            const auto stage = package->stage(node);
            const auto& kind = stage.metadata.kind;
            require(stage.operator_id == "maxsim." + kind && stage.operator_version == "1" &&
                        stage.metadata.context == "maxsim.logical/1" &&
                        stage.metadata.decomposition_rule == "none/1",
                    "unsupported MaxSim IR mapping");
            if (kind == "read" || kind == "score") {
                const auto index = stage.metadata.features.at("candidate_index").first;
                const auto& document = row.at("documents").at(index);
                require(stage.key.op == kind + "-" + std::to_string(index) &&
                            stage.metadata.features.at("document_id").first == document.at("document_id") &&
                            stage.metadata.work.at("document_bytes").first == document_bytes,
                        "MaxSim stage candidate or Work mismatch");
                if (kind == "read") {
                    read_plan(stage, document);
                    checked_requests += documents.at(query * count + index).plans.size();
                } else {
                    auto& flop = documents.at(query * count + index).flop;
                    flop = stage.metadata.work.at("dot_product_flop").first;
                    require(flop && flop == derived.at("document_flop"), "MaxSim scoring Work mismatch");
                    checked_flop = checked_add(checked_flop, flop);
                }
            } else {
                require((kind == "input" || kind == "topk") && kind == stage.key.op,
                        "unknown MaxSim stage");
            }
        }
    }
    require(!std::getline(ledger, line), "extra logical read ledger rows");
    require(checked_requests == total_queries * expected_reads && checked_flop == total_queries * query_flop &&
                std::all_of(documents.begin(), documents.end(), [](const auto& d) {
                    return d.flop && !d.plans.empty();
                }), "MaxSim request/scoring coverage mismatch");

    rules.add(protocol, [this](const auto& m) -> timing::MessageSize {
        require(m.count && m.count <= count && (hybrid || m.count == count) && m.query < queries.size(),
                "MaxSim message identity");
        if (m.kind == MessageKind::Execute) {
            const auto bytes = command_bytes(m.count);
            return {bytes, checked_add(bytes, command_header)};
        }
        if (m.kind == MessageKind::VectorBackfill) {
            require(fill.path == FillPath::Staged && m.count == 1, "invalid MaxSim vector backfill");
            return {document_bytes, document_bytes};
        }
        require(m.kind == MessageKind::Result, "unsupported MaxSim message");
        const auto bytes = m.count * score_bytes;
        return {bytes, checked_add(bytes, result_header)};
    });
    for (auto kind : {MessageKind::Execute, MessageKind::Result, MessageKind::VectorBackfill}) {
        const bool vector = kind == MessageKind::VectorBackfill;
        if (vector && fill.path != FillPath::Staged)
            continue;
        auto size = rules.size({protocol, kind, 0, 1, 1, 1, 1, vector ? 1 : count});
        // MQSim's configured TLP payload and fixed 28 B overhead.
        require(size.payload + 28 * ceil_div(size.payload, g.pcie_max_payload_bytes) <= INT32_MAX,
                "MaxSim protocol envelope exceeds link bounds");
    }

    if (!offload) {
        cache_pages = integer(cache.at("host_capacity_bytes")) / document_bytes;
        if (initial_cache) {
            const auto& initial = initial_cache->at("cache_oldest_first");
            require(initial.is_array() && initial.size() <= cache_pages, "MaxSim cache snapshot exceeds capacity");
            for (const auto& id : initial) {
                const auto doc = integer(id);
                require(doc < integer(c.at("workload").at("corpus_documents")) && !cached.count(doc),
                        "invalid MaxSim cache snapshot");
                touch(doc);
            }
        }
    }
    if (hybrid)
        c.at("cache").at("host_fill_on_miss") = fill.path != FillPath::None;
    if (fill.path != FillPath::None)
        configure_backfill(run.at("backfill"), integer(c.at("workload").at("corpus_documents")));
    for (auto& d : documents)
        d.controller = offload;
    const auto geometry = J::parse(config->effective_json());
    install_cost(c.at("compute"), integer(derived.at("document_flop")));
    for (auto& side : sides)
        side.buffer = std::make_unique<timing::BufferModel>(bridge, now, buffer_limit);
    require(!fs::exists(output), "trace output must be new");
    trace.open(output);
    J configuration = {{"type", "configuration"}, {"placement", placement}, {"effective_config", c},
          {"local_read_die_admission", local_read_die_admission},
          {"nvme_read_die_admission", nvme_read_die_admission},
          {"effective_platform", platform}, {"device", geometry}, {"layout_sha256", layout->source_sha256()},
          {"device_sha256", config->source_sha256()}, {"package_sha256", ir.package_sha256},
          {"registry_sha256", ir.registry_sha256}, {"receipt_sha256", run.at("receipt_sha256")},
          {"mqsim_commit", mqsim_commit}, {"mqsim_source_sha256", mqsim_source_sha256},
          {"cost_profile_sha256", cost_profile->source_sha256()},
          {"mqsim_pcie_configured_GBps", g.lanes * g.lane_bandwidth},
          {"configured_unvalidated", true}};
    if (fill.path != FillPath::None)
        configuration["backfill"] = run.at("backfill");
    emit(configuration);

    if (preflight_only) {
        emit({{"type", "device_preflight"}, {"success", true}, {"allocated", false},
              {"mapped_end_sectors", layout->mapped_end_sectors()}, {"preload_sectors", preload},
              {"preload_pages", preload / g.page_sectors}, {"logical_reads", checked_requests}});
        trace.flush();
        require(bool(trace), "cannot flush MaxSim preflight");
        reported = true;
        return;
    }
    device = std::make_unique<OrdinaryDevice>(preload, *config, *layout);
    if (local_read_die_admission || nvme_read_die_admission) {
        auto* ftl = dynamic_cast<SSD_Components::FTL*>(device->device->Firmware);
        die_admission_tsu = ftl ? dynamic_cast<SSD_Components::TSU_Priority_OutOfOrder*>(ftl->TSU) : nullptr;
        require(die_admission_tsu, "die admission requires the priority out-of-order TSU");
    }
    local = std::make_unique<timing::MetadataLocalReads>(
        *static_cast<SSD_Components::Host_Interface_NVMe*>(device->device->Host_interface), bridge, now);
    pcie = std::make_unique<timing::MetadataPcie>(*device->link, bridge, now, rules);
    scheduling.admission = scheduling.workers = concurrency;
    connect();

    auto* phy = dynamic_cast<SSD_Components::NVM_PHY_ONFI*>(device->device->PHY);
    require(phy && !active, "MaxSim requires one MQSim ONFI device");
    phy->ConnectToTransactionServicedSignal(&nand_completed);
    active = this;
}

void Run::State::install_cost(const J& compute, uint64_t flop) {
    const auto launch = integral_scale(compute.at("batch_launch_us"), 1000);
    const auto max_docs = std::min<uint64_t>(batch_limit, count);
    require(flop <= UINT64_MAX / max_docs, "MaxSim batch model domain");
    target = {"maxsim-e0", "configured MaxSim E0", "simulated", "effective FLOP rate", "single engine", buffer_limit};
    cost::Registry registry;
    cost::register_builtin_formulas(registry);
    capture::StageSpec stage;
    stage.kind = "batch";
    stage.work_units = {{"dot_product_flop", "flop"}};
    stage.contexts = {batch_context};
    stage.decomposition_rules = {"none/1"};
    stage.output_size_rule = "none";
    registry.add_operator({batch_operator, "1", {"host", "controller"}, {stage}, "maxsim.metadata-audit/1"});
    J models = J::array();
    for (bool controller : {false, true}) {
        if (!hybrid && controller != offload)
            continue;
        const auto rate = integral_scale(compute.at(controller ? "device_effective_tflops" : "host_effective_tflops"), 1000);
        require(rate, "MaxSim batch model rate");
        models.push_back({{"operator", batch_operator}, {"operator_version", "1"}, {"stage_kind", stage.kind},
                          {"context", batch_context}, {"location", controller ? "controller" : "host"},
                          {"target_id", target.id}, {"work_units", stage.work_units},
                          {"feature_ranges", J::array({{{"name", "dot_product_flop"}, {"unit", "flop"},
                                                       {"minimum", flop}, {"maximum", max_docs * flop}}})},
                          {"source", "configured"}, {"parameter_source", "MaxSim E0 effective compute configuration"},
                          {"formula", "integer-rate/1"},
                          {"parameter_schema_sha256", registry.parameter_schema_sha256("integer-rate/1")},
                          {"parameters", {{"setup_ns", free ? 0 : launch}, {"units_per_ns", rate},
                                          {"weights", J::array({free ? 0 : 1})}}}});
    }
    J profile = {{"format", "ndpsim-cost-profile/1"}, {"fixture", true},
                 {"target", {{"id", target.id}, {"hardware", target.hardware}, {"os", target.os},
                             {"frequency", target.frequency}, {"affinity", target.affinity},
                             {"memory_bytes", target.memory_bytes}}},
                 {"rules", models}};
    const auto raw = profile.dump();
    cost_profile = cost::Profile::parse(raw, capture::sha256(raw), registry, target, true);
    for (bool controller : {false, true}) {
        if (hybrid || controller == offload) {
            (void)demand(flop, controller);
            (void)demand(max_docs * flop, controller);
        }
    }
}

uint64_t Run::State::demand(uint64_t flop, bool controller) const {
    cost::Input input;
    input.key = {0, "score-batch", "s0"};
    input.operator_id = batch_operator;
    input.operator_version = "1";
    input.stage_kind = "batch";
    input.context = batch_context;
    input.location = controller ? "controller" : "host";
    input.work = {{"dot_product_flop", {flop, "flop"}}};
    input.target = target;
    return cost_profile->demand(input);
}

void Run::State::read_plan(const dag::Stage& stage, const J& candidate) {
    const auto index = stage.metadata.features.at("candidate_index").first;
    auto& doc = documents.at(stage.key.query * count + index);
    require(candidate.at("candidate_index") == index && doc.plans.empty(), "duplicate/misordered read plan");
    doc.id = candidate.at("document_id");
    doc.key = stage.key;
    const auto& accesses = stage.metadata.accesses;
    const auto& requests = candidate.at("requests");
    require(!accesses.empty() && accesses.size() == requests.size() &&
                accesses.size() == stage.metadata.work.at("logical_reads").first,
            "wrong logical request count");
    uint64_t bytes = 0;
    for (size_t i = 0; i < accesses.size(); ++i) {
        const auto& request = requests.at(i);
        const auto& a = accesses.at(i);
        require(a.offset_bytes == request.at("offset_bytes") && a.length_bytes == request.at("valid_bytes"),
                "read ledger/IR access mismatch");
        auto plan = layout->resolve_request(a, integer(request.at("request_bytes")));
        const auto& read = plan.reads.front();
        require(read.sectors <= 65535 && read.lba < preload && read.sectors <= preload - read.lba,
                "MaxSim request outside NVMe/preloaded range");
        bytes = checked_add(bytes, a.length_bytes);
        doc.plans.push_back(std::move(plan));
    }
    require(bytes == document_bytes, "document read plan incomplete");
    doc.reads_left = doc.moves_left = accesses.size();
}

void Run::State::connect() {
    runtime.observations().enabled = false;
    host.observations().enabled = false;
    command_service.observations().enabled = false;
    for (bool controller : {false, true}) {
        auto& side = sides[controller];
        side.scoring.observations().enabled = false;
        side.buffer->observations().sink = [this, controller](const auto& x) {
            if (detailed) emit({{"type", "input_buffer"}, {"time_ns", x.time}, {"candidate", std::stoull(x.lifetime)},
                  {"side", controller ? "controller" : "host"}, {"credit", x.id},
                  {"bytes", x.bytes}, {"used", x.used}, {"reserve", x.reserve}});
        };
    }
    staging.observations().sink = [this](const auto& x) {
        if (detailed) emit({{"type", "controller_staging"}, {"key", x.key}, {"submitted_ns", x.submitted},
              {"start_ns", x.start}, {"end_ns", x.end}, {"duration_ns", x.duration_ns}});
    };
    device->root->data_arrived = [this](auto address, auto bytes) {
        device->flow->data(address, bytes);
        if (launched)
            dma += bytes;
    };
    device->flow->completed = [this](auto x) {
        if (!launched)
            return;
        auto it = ordinary.find(x.token);
        require(it != ordinary.end() && !x.write && x.time == now(), "orphan ordinary completion");
        const auto binding = it->second;
        const auto& request = documents.at(binding.candidate).plans.at(binding.slice).reads.front();
        require(x.lba == request.lba && x.sectors == request.sectors, "ordinary response differs from plan");
        ordinary.erase(it);
        bridge.enqueue(x.time, EventBridge::ResourceCompleteRelease, "MaxSim data and CQ", [this, binding] {
            complete_read(binding.candidate, binding.slice);
        });
    };
    pcie->observations().sink = [this](const auto& x) {
        const bool vector = x.kind == MessageKind::VectorBackfill;
        if (vector) {
            fill.vector_bytes += x.logical;
        } else {
            ++protocol_messages;
            (x.kind == MessageKind::Execute ? command_payload : result_payload) += x.logical;
        }
        if (detailed) {
            const char* kind = vector ? "backfill" : x.kind == MessageKind::Execute ? "command" : "result";
            J entry = {{"type", "protocol"}, {"kind", kind},
                       {"direction", x.direction == timing::Direction::H2D ? "H2D" : "D2H"},
                       {"link_platform_id", x.id}, {"query", x.query}, {"candidates", x.count},
                       {"effective_payload_bytes", x.logical}, {"header_bytes", x.payload - x.logical},
                       {"link_payload_bytes", x.payload}, {"wire_bytes", x.wire},
                       {"submitted_ns", x.submitted}, {"arrived_ns", x.arrived}};
            if (vector)
                entry["candidate"] = x.chunk;
            emit(entry);
        }
    };
    device->link->observe = [this](const char* event, const auto& x) {
        using namespace Host_Components;
        if (!launched)
            return;
        const bool upload = x.destination == PCIe_Destination_Type::DEVICE;
        const char* layer = "platform";
        switch (x.type) {
        case PCIe_Message_Type::READ_REQ: layer = "sq_fetch"; break;
        case PCIe_Message_Type::READ_COMP: layer = "sq_entry"; break;
        case PCIe_Message_Type::WRITE_REQ:
            layer = upload ? "doorbell" : (x.payload == 16 ? "cq_entry" : "data");
            break;
        default: break;
        }
        if (std::string_view(event) == "arrive") {
            auto& accounting = link_ledger[upload ? "H2D" : "D2H"];
            ++accounting.messages;
            accounting.payload += x.payload;
            accounting.wire += x.wire;
            accounting.service += x.service;
        }
        if (detailed) emit({{"type", "pcie"}, {"event", event}, {"layer", layer},
              {"id", x.id}, {"platform_id", x.platform_id}, {"time_ns", x.time},
              {"direction", upload ? "H2D" : "D2H"}, {"payload_bytes", x.payload},
              {"wire_bytes", x.wire}, {"service_ns", x.service}});
    };
    observation.emit = [this](bool stat, const std::string& row) {
        emit({{"type", stat ? "nand_interval_union" : "nand_interval"}, {"mqsim", J::parse(row)}});
    };
}

void Run::State::start_executor() {
    for (auto& offset : scheduling.offered_ns)
        offset = checked_add(origin, offset);
    executor = std::make_unique<dag::Executor>(*package, runtime, bridge, now, std::move(scheduling),
        [this](const auto& stage) { return lower(stage); });
    executor->stages().sink = [this](const auto& x) {
        if (detailed) {
            J entry = {{"type", "stage"}, {"op", x.key.op}, {"submitted_ns", x.submitted},
                       {"visible_ns", x.completed}, {"success", x.success}};
            if (experiment)
                entry["query"] = x.key.query;
            emit(entry);
        }
    };
    executor->requests().sink = [this](const auto& x) {
        visible = x.visible;
        active_queries.erase(x.query);
        J record = {{"type", "query"}, {"offered_ns", experiment && !open_loop ? x.worker_start : x.offered},
                    {"visible_ns", x.visible},
                    {"drained_ns", x.drained}, {"success", x.success}};
        if (experiment)
            record["query"] = x.query;
        if (hybrid) {
            auto& q = queries.at(x.query);
            record["offloaded_candidates"] = offloaded(q);
            q.routes = {};
        }
        if (open_loop)
            record["admitted_ns"] = x.admitted;
        emit(record);
    };
    executor->start([this](auto result) {
        require(!result.failure, result.failure ? result.failure->detail.c_str() : "");
        finished = true;
    });
}

timing::Operation Run::State::lower(const dag::Stage& stage) {
    const auto& s = stage.metadata;
    timing::Operation op;
    op.key = stage.key;
    op.work = s.work;
    op.features = s.features;
    op.outputs = s.outputs;
    // Distinct real services are accounted by the adapters. The Runtime owns
    // zero-duration DAG publication and the common Host Top-K stage.
    op.resource = &host;
    const auto query = stage.key.query;
    auto& q = queries.at(query);
    if (s.kind == "input") {
        require(active_queries.emplace(query, query).second, "duplicate MaxSim query start");
        if (hybrid) {
            route(query);
            if (offloaded(q)) {
                // Publish the common query input independently of the command flight.
                // The controller's read queue is gated by command arrival.
                op.send_return = [this, query](Done done) {
                    command_service.submit(std::to_string(query), command_ns, [] {}, [this, query](bool ok) {
                        require(ok, "MaxSim command service failed");
                        send_command(query);
                    });
                    done({});
                };
            }
        } else if (offload) {
            op.duration_ns = command_ns;
            op.send_return = [this, query](Done done) { send_command(query, std::move(done)); };
        }
    } else if (s.kind == "read") {
        const auto index = query * count + s.features.at("candidate_index").first;
        op.input = [this, index](Done done) {
            auto& d = documents.at(index);
            require(!d.read_done && !d.submitted, "duplicate document read");
            d.read_done = std::move(done);
        };
    } else if (s.kind == "score") {
        const auto index = query * count + s.features.at("candidate_index").first;
        op.input = [this, index, query](Done done) {
            const auto& d = documents.at(index);
            require(!d.reads_left && !d.moves_left && d.credit && !scored.at(index) &&
                        queries.at(query).ready[d.controller].emplace(std::make_pair(now(), index), std::move(done)).second,
                    "document score queued before input or more than once");
            if (detailed) emit({{"type", "score_ready"}, {"candidate", index}, {"time_ns", now()}});
        };
        op.send_return = [this, index, query](Done done) {
            require(scored.at(index), "score before batch completion");
            if (!documents.at(index).controller) {
                done({});
                return;
            }
            auto& returns = queries.at(query).awaiting_return;
            require(returns.emplace(index, std::move(done)).second, "duplicate score return");
            if (returns.size() == offloaded(queries.at(query)))
                send_result(query);
        };
    } else if (s.kind == "topk") {
        require(q.host_scores == uplink, "Top-K before all scores reached the Host");
        op.send_return = [this, query](Done done) {
            ++topk_count;
            if (detailed) {
                J entry = {{"type", "topk"}, {"time_ns", now()}, {"descriptor_bytes", topk_bytes}};
                if (experiment)
                    entry["query"] = query;
                emit(entry);
            }
            queries.at(query).host_scores = 0;
            host_scores -= uplink;
            occupancy(query);
            done({});
        };
    }
    return op;
}

void Run::State::poll() {
    local->poll();
    if (!launched && device->window->advance(!Simulator->Peek_next_time(), now())) {
        launched = true;
        origin = device->window->origin();
        observation.reset_window();
        if (local_read_die_admission)
            die_admission_tsu->Enable_local_read_die_admission();
        if (nvme_read_die_admission)
            die_admission_tsu->Enable_host_read_die_admission();
        emit({{"type", "measurement_start"}, {"time_ns", origin}, {"preload_sectors", preload},
              {"preload_pages", preload / config->geometry().page_sectors}});
        start_executor();
    }
    // Settle every continuation at this SST timestamp before choosing a batch.
    // MetadataLocalReads may have just moved backend completions into the bridge.
    if (!launched || bridge.pending_now())
        return;
    // A result made ready at this timestamp enters PCIe before newly ready backfills.
    pump_backfill();
    if (finished) {
        if (bridge.idle() && !reported) {
            blocked(nullptr, 0);
            report();
        }
        return;
    }
    pump_scores(false);
    pump_scores(true);
    if (hybrid || overlap || !sides[offload].scoring.pending())
        pump_reads();
}

size_t Run::State::offloaded(const Query& q) const {
    return hybrid ? q.controller_candidates : offload ? count : 0;
}

uint64_t Run::State::command_bytes(size_t candidates) const {
    return checked_add(query_vector_bytes, candidates * document_id_bytes);
}

void Run::State::route(size_t query) {
    auto& q = queries.at(query);
    for (size_t candidate = 0; candidate < count; ++candidate) {
        const auto index = query * count + candidate;
        auto& d = documents.at(index);
        d.hit = cached.count(d.id);
        const char* decision = nullptr;
        if (fill.path != FillPath::None) {
            if (fill.policy != Admission::Second)
                observe_access(d.id);
            if (!d.hit)
                decision = admit_backfill(index);
            if (fill.policy == Admission::Second)
                observe_access(d.id);
        }
        d.controller = !d.hit && !(d.fill && fill.path == FillPath::Ordinary);
        q.routes[d.controller].push_back(index);
        if (d.hit)
            touch(d.id);
        if (detailed) {
            J entry = {{"type", "candidate_route"}, {"query", query}, {"candidate", index}, {"document_id", d.id},
                       {"time_ns", now()}, {"path", d.hit ? "cache" : d.controller ? "local" : "ordinary"}};
            if (decision)
                entry["backfill_decision"] = decision;
            emit(entry);
        }
    }
    q.controller_candidates = q.routes[true].size();
}

void Run::State::configure_backfill(const J& spec, uint64_t corpus_documents) {
    require(spec.is_object() && spec.size() == 5, "MaxSim backfill fields");
    const auto& policy = spec.at("policy");
    if (policy == "second")
        fill.policy = Admission::Second;
    else if (policy == "frequency")
        fill.policy = Admission::Frequency;
    else
        require(policy == "all", "unsupported MaxSim cache admission policy");
    fill.history_limit = integer(spec.at("history_window_accesses"));
    fill.window_ns = integer(spec.at("budget_window_ns"));
    fill.budget_bytes = integer(spec.at("budget_bytes"));
    const auto& history = spec.at("initial_history_oldest_first");
    require(fill.window_ns && (fill.policy == Admission::All || fill.history_limit) &&
                history.is_array() && history.size() <= fill.history_limit, "invalid MaxSim backfill window");
    for (const auto& entry : history) {
        const auto id = integer(entry);
        require(id < corpus_documents, "invalid MaxSim history document");
        observe_access(id);
    }
}

void Run::State::observe_access(uint64_t id) {
    if (fill.policy == Admission::All)
        return;
    if (fill.history.size() == fill.history_limit) {
        auto old = fill.frequency.find(fill.history.front());
        if (!--old->second)
            fill.frequency.erase(old);
        fill.history.pop_front();
    }
    fill.history.push_back(id);
    ++fill.frequency[id];
}

uint64_t Run::State::access_frequency(uint64_t id) const {
    const auto entry = fill.frequency.find(id);
    return entry == fill.frequency.end() ? 0 : entry->second;
}

const char* Run::State::admit_backfill(size_t index) {
    auto& d = documents.at(index);
    if (fill.inflight.count(d.id)) {
        ++fill.deduplicated;
        return "inflight";
    }
    if (!cache_pages)
        return "capacity";
    if (fill.policy == Admission::Second && !fill.frequency.count(d.id))
        return "history";
    if (fill.policy == Admission::Frequency && cached.size() == cache_pages &&
        access_frequency(d.id) <= access_frequency(cache_order.front()))
        return "frequency";
    const auto epoch = (now() - origin) / fill.window_ns;
    if (epoch != fill.epoch) {
        fill.epoch = epoch;
        fill.used = 0;
    }
    if (document_bytes > fill.budget_bytes - fill.used)
        return "budget";
    fill.used += document_bytes;
    ++fill.admitted;
    if (fill.path == FillPath::Ordinary)
        ++fill.ordinary_documents;
    require(fill.inflight.emplace(d.id, index).second, "duplicate MaxSim backfill");
    d.fill = true;
    return "admitted";
}

void Run::State::complete_backfill(size_t index) {
    const auto& d = documents.at(index);
    require(fill.inflight.at(d.id) == index && !cached.count(d.id), "invalid MaxSim backfill completion");
    fill.inflight.erase(d.id);
    ++fill.completed;
    fill.last_completion = now();
    J evicted = nullptr;
    bool publish = true;
    if (cached.size() == cache_pages) {
        const auto victim = cache_order.front();
        if (fill.policy == Admission::Frequency)
            publish = access_frequency(d.id) > access_frequency(victim);
        if (publish)
            evicted = victim;
    }
    if (publish) {
        touch(d.id);
        ++fill.published;
    }
    if (detailed)
        emit({{"type", "backfill_complete"}, {"candidate", index}, {"document_id", d.id},
              {"time_ns", now()}, {"published", publish}, {"evicted_document_id", evicted}});
}

void Run::State::pump_backfill() {
    for (const auto index : fill.ready) {
        pcie->send({protocol, MessageKind::VectorBackfill, index / count, index + 2, 1, 1, index, 1},
            [this, index](const auto&) {
                auto& d = documents.at(index);
                require(d.credit && scored.at(index), "MaxSim vector returned without scored input");
                complete_backfill(index);
                sides[true].buffer->release(d.credit);
                d.credit = 0;
            });
    }
    fill.ready.clear();
}

void Run::State::send_command(size_t query, Done done) {
    const auto candidates = offloaded(queries.at(query));
    if (detailed) emit({{"type", "offload_command_service"}, {"query", query},
                        {"duration_ns", command_ns}, {"end_ns", now()}});
    pcie->send({protocol, MessageKind::Execute, query, 1, 1, 1, 1, candidates},
        [this, query, candidates, done = std::move(done)](const auto&) {
            auto& q = queries.at(query);
            q.command_arrived = true;
            q.controller_input = command_bytes(candidates);
            controller_input += q.controller_input;
            occupancy(query);
            if (done)
                done({});
        });
}

void Run::State::touch(uint64_t id) {
    if (!cache_pages)
        return;
    if (auto it = cached.find(id); it != cached.end()) {
        cache_order.splice(cache_order.end(), cache_order, it->second);
        return;
    }
    require(!hybrid || !launched || fill.path != FillPath::None,
            "hybrid cache membership changed during execution");
    if (cached.size() == cache_pages) {
        cached.erase(cache_order.front());
        cache_order.pop_front();
    }
    cached.emplace(id, cache_order.insert(cache_order.end(), id));
}

void Run::State::blocked(const char* reason, size_t index) {
    const std::string next = reason ? reason : "";
    if (next == last_block)
        return;
    if (!last_block.empty())
        blocked_ns[last_block] += now() - blocked_since;
    blocked_since = now();
    last_block = next;
    if (reason && detailed)
        emit({{"type", "backpressure"}, {"reason", reason}, {"time_ns", now()},
              {"candidate", index}, {"outstanding_reads", qd},
              {"window_documents", window}, {"input_buffer_bytes", sides[documents.at(index).controller].buffer->used()}});
}

void Run::State::pump_reads() {
    while (!active_queries.empty() && !reserving) {
        auto it = active_queries.lower_bound(next_read_query);
        size_t index = 0;
        const char* reason = nullptr;
        bool found = false;
        for (size_t attempt = 0; attempt < active_queries.size(); ++attempt) {
            if (it == active_queries.end())
                it = active_queries.begin();
            const auto query = it++->first;
            const auto& q = queries.at(query);
            for (bool controller : {false, true}) {
                if ((!hybrid && controller != offload) ||
                    (hybrid && controller && !q.command_arrived))
                    continue;
                const auto& side = sides[controller];
                if (!overlap && side.scoring.pending())
                    continue;
                const auto next = q.next_document[controller];
                if (next == (hybrid ? q.routes[controller].size() : count))
                    continue;
                const auto candidate = hybrid ? q.routes[controller][next] : query * count + next;
                const auto& d = documents.at(candidate);
                if (!d.read_done)
                    continue;
                const bool hit = !d.credit && (hybrid ? d.hit : cached.count(d.id));
                const char* denied = !hit && qd == qd_limit ? "qd"
                    : !hit && !d.credit && q.window == window_limit ? "window"
                    : !d.credit && document_bytes > buffer_limit - side.buffer->used() ? "buffer" : nullptr;
                if (denied) {
                    index = candidate;
                    reason = denied;
                    continue;
                }
                found = true;
                index = candidate;
                break;
            }
            if (found)
                break;
        }
        blocked(found ? nullptr : reason, index);
        if (!found)
            return;
        const auto query = index / count;
        auto& q = queries.at(query);
        auto& d = documents.at(index);
        const bool controller = d.controller;
        auto& side = sides[controller];
        if (!d.credit) {
            const bool hit = hybrid ? d.hit : cached.count(d.id);
            if (hit && !hybrid)
                touch(d.id);
            reserving = true;
            side.buffer->reserve(document_bytes, std::to_string(index), [this, index, query, hit](auto credit) {
                auto& document = documents.at(index);
                auto& current = queries.at(query);
                document.credit = credit;
                reserving = false;
                if (hit) {
                    ++hits;
                    ++current.next_document[false];
                    ++next_document;
                    next_read_query = query + 1;
                    document.reads_left = 0;
                    document.moves_left = 1;
                    moved(index);
                    return;
                }
                ++window;
                peak_window = std::max(peak_window, ++current.window);
                if (detailed)
                    emit({{"type", "read_window"}, {"candidate", index}, {"time_ns", now()},
                          {"used", window}, {"reserve", true}});
            });
            return;
        }
        const auto slice = d.submitted++;
        const auto& plan = d.plans.at(slice);
        const auto& request = plan.reads.front();
        peak_qd = std::max(peak_qd, ++qd);
        ++submitted_reads;
        ++side.outstanding;
        if (detailed) emit({{"type", "read_submit"}, {"candidate", index}, {"document_id", d.id}, {"slice", slice},
              {"lba", request.lba}, {"sectors", request.sectors}, {"time_ns", now()},
              {"valid_bytes", plan.logical_bytes}, {"outstanding_reads", qd},
              {"path", controller ? "local" : "ordinary"}});
        if (controller) {
            local->submit(plan, d.key, [this, index, slice](auto completion) {
                require(completion.requests.size() == 1, "local request unexpectedly split");
                complete_read(index, slice);
            });
        } else {
            Simulator->Synchronize_now(now());
            auto token = device->window->read(request.lba, request.sectors);
            require(ordinary.emplace(token, OrdinaryRead{index, slice}).second, "duplicate ordinary token");
        }
        if (d.submitted == d.plans.size()) {
            ++q.next_document[controller];
            ++next_document;
            next_read_query = query + 1;
        }
    }
}

void Run::State::complete_read(size_t index, size_t slice) {
    auto& d = documents.at(index);
    require(qd && d.reads_left && d.credit, "read completion without in-flight resources");
    --qd;
    --sides[d.controller].outstanding;
    ++completed_reads;
    const auto& plan = d.plans.at(slice);
    read_bytes += plan.sector_bytes;
    effective_bytes += plan.logical_bytes;
    if (d.controller)
        ++local_reads;
    else
        ordinary_bytes += plan.sector_bytes;
    if (detailed) emit({{"type", "read_complete"}, {"candidate", index}, {"slice", slice}, {"time_ns", now()},
          {"outstanding_reads", qd}});
    if (!--d.reads_left) {
        require(window > 0, "document read-window underflow");
        --window;
        --queries.at(index / count).window;
        if (d.fill && fill.path == FillPath::Ordinary)
            complete_backfill(index);
        else if (!hybrid && !d.controller)
            touch(d.id);
        if (detailed) emit({{"type", "read_window"}, {"candidate", index}, {"time_ns", now()},
              {"used", window}, {"reserve", false}});
    }
    if (d.controller) {
        const auto bytes = plan.logical_bytes;
        staged_bytes += bytes;
        staging.submit(std::to_string(index) + "/" + std::to_string(slice),
                       ceil_div(bytes, port_rate), [] {}, [this, index](bool success) {
            require(success, "controller staging failed");
            moved(index);
        });
    } else {
        moved(index);
    }
}

void Run::State::moved(size_t index) {
    auto& d = documents.at(index);
    require(d.moves_left > 0, "duplicate document movement");
    if (!--d.moves_left) {
        require(!d.reads_left && bool(d.read_done), "document input published before all reads");
        auto done = std::move(d.read_done);
        done({});
    }
}

void Run::State::pump_scores(bool controller) {
    auto& side = sides[controller];
    if (side.scoring.pending() || active_queries.empty() ||
        (!overlap && (side.outstanding || (controller && staging.pending()))))
        return;
    auto it_query = active_queries.lower_bound(side.next_score_query);
    size_t query = 0;
    for (size_t attempt = 0;; ++attempt) {
        if (attempt == active_queries.size())
            return;
        if (it_query == active_queries.end())
            it_query = active_queries.begin();
        query = it_query++->first;
        if (!queries.at(query).ready[controller].empty())
            break;
    }
    side.next_score_query = query + 1;
    auto& ready = queries.at(query).ready[controller];
    std::vector<size_t> members;
    std::vector<Done> completions;
    uint64_t flop = 0;
    for (auto it = ready.begin(); it != ready.end() && members.size() < batch_limit; it = ready.erase(it)) {
        const auto index = it->first.second;
        members.push_back(index);
        completions.push_back(std::move(it->second));
        flop = checked_add(flop, documents.at(index).flop);
    }
    const auto duration = demand(flop, controller);
    const auto batch = ++batches;
    flops = checked_add(flops, flop);
    compute_ns = checked_add(compute_ns, duration);
    ++side.batches;
    side.compute_ns = checked_add(side.compute_ns, duration);
    side.scoring.submit(std::to_string(batch), duration, [this, batch, members, flop, duration, controller] {
        peak_scoring_engines = std::max(peak_scoring_engines, sides[false].scoring.busy() + sides[true].scoring.busy());
        if (detailed) emit({{"type", "score_batch"}, {"batch", batch}, {"candidates", members},
              {"flop", flop}, {"side", controller ? "controller" : "host"}, {"start_ns", now()}, {"end_ns", checked_add(now(), duration)},
              {"duration_ns", duration}});
    }, [this, query, members, controller, completions = std::move(completions)](bool ok) {
        require(ok, "MaxSim scoring failed");
        auto& q = queries.at(query);
        auto& current_side = sides[controller];
        current_side.scored += members.size();
        for (size_t i = 0; i < members.size(); ++i) {
            const auto index = members.at(i);
            auto& d = documents.at(index);
            require(!scored.at(index) && d.credit, "duplicate scoring or missing buffer credit");
            scored.at(index) = true;
            if (d.fill && fill.path == FillPath::Staged) {
                fill.ready.push_back(index);
            } else {
                current_side.buffer->release(d.credit);
                d.credit = 0;
            }
            if (controller) {
                q.controller_scores += score_bytes;
                controller_scores += score_bytes;
            } else {
                q.host_scores += score_bytes;
                host_scores += score_bytes;
            }
            completions.at(i)({});
        }
        occupancy(query);
    });
}

void Run::State::send_result(size_t query) {
    auto& q = queries.at(query);
    const auto candidates = offloaded(q);
    const auto bytes = candidates * score_bytes;
    require(candidates && q.controller_scores == bytes && q.controller_input == command_bytes(candidates),
            "incomplete controller scores");
    controller_input -= q.controller_input;
    q.controller_input = 0;
    occupancy(query);
    pcie->send({protocol, MessageKind::Result, query, 1, 1, 1, 1, candidates}, [this, query, bytes](const auto&) {
        auto& current = queries.at(query);
        current.host_scores += bytes;
        current.controller_scores = 0;
        host_scores += bytes;
        controller_scores -= bytes;
        occupancy(query);
        auto completions = std::move(current.awaiting_return);
        current.awaiting_return.clear();
        for (const auto& entry : completions)
            entry.second({});
    });
}

void Run::State::occupancy(size_t) {
    peak_controller_input = std::max(peak_controller_input, controller_input);
    peak_controller_scores = std::max(peak_controller_scores, controller_scores);
    peak_host_scores = std::max(peak_host_scores, host_scores);
    if (detailed) emit({{"type", "metadata_buffers"}, {"time_ns", now()}, {"controller_query_ids_bytes", controller_input},
          {"controller_score_bytes", controller_scores}, {"host_score_bytes", host_scores}});
}

void Run::State::verify() const {
    require(finished && bridge.idle() && executor->drained() && !runtime.task_records() &&
                !runtime.owners() && !runtime.pins() && !host.pending() && !command_service.pending() &&
                !staging.pending() && active_queries.empty() &&
                std::all_of(queries.begin(), queries.end(), [](const auto& q) {
                    return !q.window && q.routes[false].empty() && q.routes[true].empty() &&
                           q.ready[false].empty() && q.ready[true].empty() &&
                           q.awaiting_return.empty() && !q.controller_input && !q.controller_scores && !q.host_scores;
                }),
            "MaxSim DAG, tokens or services not drained");
    require(std::all_of(sides.begin(), sides.end(), [](const auto& side) {
                return !side.outstanding && !side.scoring.pending() && !side.buffer->used() &&
                       !side.buffer->waiting() && !side.buffer->live();
            }), "MaxSim side resources not drained");
    require(!qd && !window && !reserving &&
                ordinary.empty() && !device->flow->pending() && !local->pending() &&
                !local->pending_requests() && !pcie->pending() && !Simulator->Peek_next_time() &&
                !controller_scores && !controller_input && !host_scores,
            "MaxSim reads, messages or credit not drained");
    require(hits <= documents.size() && (!offload || !hits) &&
                completed_reads == (documents.size() - hits) * (expected_reads / count) &&
                effective_bytes == (documents.size() - hits) * document_bytes &&
                submitted_reads == completed_reads && next_document == documents.size() &&
                flops == queries.size() * query_flop && topk_count == queries.size() &&
                std::all_of(scored.begin(), scored.end(), [](bool done) { return done; }) &&
                visible >= origin && now() >= visible && Simulator->Time() <= now(),
            "MaxSim terminal or Work accounting disagreement");
    const uint64_t commands = std::count_if(queries.begin(), queries.end(), [this](const auto& q) {
        return offloaded(q) > 0;
    });
    const auto offloaded_documents = sides[true].scored;
    require(fill.inflight.empty() && fill.ready.empty() && fill.admitted == fill.completed &&
                fill.published <= fill.completed && fill.last_completion <= now() &&
                (fill.path != FillPath::Ordinary || fill.ordinary_documents == fill.completed) &&
                (fill.path != FillPath::Staged || fill.vector_bytes == fill.completed * document_bytes),
            "MaxSim backfill resources or ledger not drained");
    require(dma == ordinary_bytes &&
                (!hybrid || (completed_reads - local_reads == fill.ordinary_documents * (expected_reads / count) &&
                             hits + offloaded_documents + fill.ordinary_documents == documents.size())) &&
                local_reads == offloaded_documents * (expected_reads / count) &&
                staged_bytes == offloaded_documents * document_bytes &&
                protocol_messages == 2 * commands &&
                command_payload == commands * query_vector_bytes + offloaded_documents * document_id_bytes &&
                result_payload == offloaded_documents * score_bytes,
            "MaxSim placement or protocol ledger mismatch");
}

void Run::State::report() {
    verify();
    observation.report(origin, now(), detailed);
    J link = J::object();
    for (const auto& [direction, total] : link_ledger)
        link[direction] = {{"messages", total.messages}, {"payload_bytes", total.payload},
                           {"wire_bytes", total.wire}, {"service_ns", total.service}};
    J scoring_report = J::object();
    for (bool controller : {false, true}) {
        const auto& side = sides[controller];
        scoring_report[controller ? "controller" : "host"] = {
            {"scores", side.scored}, {"batches", side.batches}, {"compute_ns", side.compute_ns},
            {"peak_input_buffer_bytes", side.buffer->peak()}};
    }
    J summary = {{"type", "summary"}, {"success", true}, {"scope", "synthetic_fixture_execution"},
          {"configured_unvalidated", true}, {"placement", placement},
          {"origin_ns", origin}, {"visible_ns", visible}, {"drained_ns", now()},
          {"query_latency_ns", visible - origin}, {"logical_reads", completed_reads},
          {"ordinary_nvme_reads", completed_reads - local_reads}, {"mqsim_local_reads", local_reads},
          {"logical_request_bytes", read_bytes}, {"effective_document_bytes", effective_bytes},
          {"document_d2h_payload_bytes", dma}, {"nand_read_transactions", nand_reads},
          {"nand_read_bytes", nand_read_bytes}, {"nand_user_read_bytes", nand_user_read_bytes},
          {"nand_read_amplification", effective_bytes ? J(double(nand_read_bytes) / effective_bytes) : J(nullptr)},
          {"controller_staging_bytes", staged_bytes}, {"scores", documents.size()}, {"topk_count", topk_count},
          {"topk_bytes", topk_bytes}, {"flop", flops}, {"batches", batches}, {"compute_ns", compute_ns},
          {"peak_outstanding_reads", peak_qd}, {"peak_read_window_documents", peak_window},
          {"peak_input_buffer_bytes", std::max(sides[false].buffer->peak(), sides[true].buffer->peak())},
          {"peak_scoring_engines", peak_scoring_engines}, {"scoring_by_side", scoring_report},
          {"command_payload_bytes", command_payload}, {"result_payload_bytes", result_payload},
          {"peak_controller_query_ids_bytes", peak_controller_input},
          {"peak_controller_score_bytes", peak_controller_scores}, {"peak_host_score_bytes", peak_host_scores},
          {"cache_hits", hits}, {"cache_hit_rate", double(hits) / documents.size()},
          {"backpressure_ns", blocked_ns}, {"peak_active_queries", executor->metrics().peak_queries},
          {"protocol_messages", protocol_messages}, {"pcie", link}, {"all_resources_drained", true}};
    if (fill.path != FillPath::None)
        summary["backfill"] = {{"admitted", fill.admitted}, {"deduplicated", fill.deduplicated},
            {"completed", fill.completed}, {"published", fill.published},
            {"ordinary_d2h_payload_bytes", dma}, {"vector_d2h_payload_bytes", fill.vector_bytes},
            {"foreground_ordinary_documents", fill.ordinary_documents}, {"last_completion_ns", fill.last_completion},
            {"background_tail_ns", fill.path == FillPath::Staged && fill.last_completion > visible
                                      ? fill.last_completion - visible : 0}};
    emit(summary);
    trace.flush();
    require(bool(trace), "cannot flush MaxSim trace");
    reported = true;
}

Run::Run(const fs::path& path, EventBridge& bridge, EventBridge::Clock now)
    : state_(std::make_unique<State>(path, bridge, std::move(now))) {}
Run::~Run() = default;
void Run::start() {
    if (finished())
        return;
    state_->device->start();
    state_->bridge.start();
}
void Run::poll() { state_->poll(); }
bool Run::finished() const { return state_->reported; }
void Run::verify() const {
    require(state_->reported, "MaxSim summary missing");
    if (state_->device)
        state_->verify();
}
} // namespace maxsim
