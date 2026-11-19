#include "run.h"
#include "build_identity.h"
#include "backend/observation.h"
#include "backend/ordinary_device.h"
#include "dag/executor.h"
#include "model/cost/cost_model.h"
#include "ssd/NVM_PHY_ONFI.h"
#include "timing/local_reads.h"
#include "timing/messages.h"

#include <cmath>
#include <fstream>
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

std::string read(const fs::path& path) {
    require(fs::is_regular_file(path) && fs::file_size(path) <= 16 * 1024 * 1024,
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
        std::vector<layout::Plan> plans;
        capture::Key key;
        Done read_done;
    };
    struct OrdinaryRead {
        size_t candidate, slice;
    };
    struct LinkLedger {
        uint64_t messages = 0, payload = 0, wire = 0, service = 0;
    };

    EventBridge& bridge;
    EventBridge::Clock now;
    timing::Runtime runtime{bridge, now};
    timing::HostCpu host{bridge, now, 1};
    timing::FifoResource scoring{bridge, now, 1}, staging{bridge, now, 1};
    std::unique_ptr<timing::BufferModel> buffer;
    timing::MessageRules rules;
    std::unique_ptr<dag::Package> package;
    std::unique_ptr<layout::Layout> layout;
    std::unique_ptr<DeviceConfig> config;
    std::unique_ptr<OrdinaryDevice> device;
    std::unique_ptr<timing::MetadataLocalReads> local;
    std::unique_ptr<timing::MetadataPcie> pcie;
    std::unique_ptr<dag::Executor> executor;
    std::optional<cost::Profile> cost_profile;
    cost::Target target;
    std::ofstream trace;
    Observation observation{false};
    std::vector<Document> documents;
    std::map<uint64_t, OrdinaryRead> ordinary;
    std::map<std::pair<uint64_t, size_t>, Done> ready;
    std::map<size_t, Done> awaiting_return;
    std::vector<bool> scored;
    std::string placement, last_block;
    bool offload = false, free = false, launched = false, finished = false, reported = false;
    bool reserving = false;
    uint64_t origin = 0, visible = 0, preload = 0;
    uint64_t window_limit = 0, qd_limit = 0, buffer_limit = 0, batch_limit = 0, port_rate = 0;
    uint64_t command_ns = 0, command_header = 0, result_header = 0, document_bytes = 0;
    uint64_t downlink = 0, uplink = 0, topk_bytes = 0, query_flop = 0, expected_reads = 0;
    uint64_t qd = 0, window = 0, peak_qd = 0, peak_window = 0, next_document = 0;
    uint64_t submitted_reads = 0, completed_reads = 0, read_bytes = 0, effective_bytes = 0;
    uint64_t dma = 0, staged_bytes = 0, batches = 0, flops = 0, compute_ns = 0;
    uint64_t controller_input = 0, controller_scores = 0, host_scores = 0;
    uint64_t peak_controller_input = 0, peak_controller_scores = 0, peak_host_scores = 0;
    uint64_t topk_count = 0, protocol_messages = 0;
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
    uint64_t demand(uint64_t flop) const;
    void connect();
    void read_plan(const dag::Stage&, const J& candidate);
    timing::Operation lower(const dag::Stage&);
    void poll();
    void pump_reads();
    void complete_read(size_t index, size_t slice);
    void moved(size_t index);
    void pump_scores();
    void send_result();
    void occupancy();
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
        run.emit({{"type", "nand_read"}, {"time_ns", run.now()}, {"bytes", bytes},
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
    require(run.at("format") == "ndpsim-maxsim-run/1" && run.size() == 8, "MaxSim run format/fields");
    placement = run.at("placement");
    offload = placement == "device_all";
    free = placement == "host_compute_free";
    require(offload || free || placement == "host", "unsupported MaxSim placement");
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
    const auto effective = bound("effective-config.json");
    const auto audit = bound("accounting.json");
    const auto structure = bound("structure.json");
    const auto registry = bound("operators.json");
    const auto ledger = bound("logical-reads.jsonl");
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
    require(package->queries().size() == 1 && package->queries().front().id == 0 &&
                ledger.at("query") == 0, "MaxSim E0 requires one query");

    const auto& c = effective.at("config");
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
    require(c.at("generation").at("queries") == 1 && load.at("mode") == "closed_loop" &&
                load.at("closed_loop_concurrency") == 1 && cache.at("initial_state") == "cold" &&
                cache.at("host_capacity_bytes") == 0 && cache.at("device_extra_capacity_bytes") == 0,
            "MaxSim E0 requires one closed-loop query and cold caches of zero capacity");
    require(ex.at("overlap_read_compute") == true && ex.at("scoring_engines_per_side") == 1 &&
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
    downlink = integer(derived.at("query_downlink_bytes"));
    uplink = integer(derived.at("query_uplink_bytes"));
    topk_bytes = integer(derived.at("topk_bytes"));
    require(window_limit && qd_limit && batch_limit && batch_limit <= 8 && port_rate &&
                document_bytes && buffer_limit >= document_bytes,
            "unsupported E0 quotas or buffer cannot hold one document");

    require(audit.at("format") == "ndpsim-maxsim-metadata-audit/1" &&
                audit.at("passed") == true && audit.at("source") == "synthetic-fixture" &&
                audit.at("queries") == 1 && audit.at("all_miss_logical_requests") == expected_reads &&
                audit.at("all_miss_request_bytes") == derived.at("query_all_miss_read_bytes") &&
                audit.at("document_effective_bytes") == derived.at("query_document_bytes") &&
                audit.at("dot_product_flop") == query_flop && audit.at("downlink_bytes_per_query") == downlink &&
                audit.at("uplink_bytes_per_query") == uplink && audit.at("topk_bytes_per_query") == topk_bytes,
            "MaxSim audit/derived accounting disagreement");
    require(ledger.at("protocol").at("downlink_bytes") == downlink &&
                ledger.at("protocol").at("uplink_bytes") == uplink, "protocol ledger mismatch");

    const auto& objects = manifest.at("objects");
    require(objects.size() == 1, "MaxSim requires one corpus object");
    const auto& object = objects.at(0);
    layout = std::make_unique<layout::Layout>(bundle / "layout.json", fs::absolute(path),
        std::vector<capture::Object>{{object.at("id"), object.at("version"), object.at("type"),
                                      object.at("size_bytes"), object.at("shape"),
                                      object.at("content_sha256")}});
    config = std::make_unique<DeviceConfig>(h.at("mqsim_config_path").get<std::string>(), fs::absolute(path));
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

    const auto count = integer(c.at("workload").at("candidate_count"));
    const auto graph = package->graph(0);
    require(count && count == ledger.at("documents").size() && count == audit.at("candidates") &&
                graph.nodes.size() == 2 * count + 2, "candidate/stage coverage mismatch");
    documents.resize(count);
    scored.resize(count);
    uint64_t checked_requests = 0, checked_flop = 0;
    for (const auto& node : graph.nodes) {
        const auto stage = package->stage(node);
        const auto& kind = stage.metadata.kind;
        require(stage.operator_id == "maxsim." + kind && stage.operator_version == "1" &&
                    stage.metadata.context == "maxsim.logical/1" &&
                    stage.metadata.decomposition_rule == "none/1",
                "unsupported MaxSim IR mapping");
        if (kind == "read" || kind == "score") {
            const auto index = stage.metadata.features.at("candidate_index").first;
            const auto& document = ledger.at("documents").at(index);
            require(stage.key.op == kind + "-" + std::to_string(index) &&
                        stage.metadata.features.at("document_id").first == document.at("document_id") &&
                        stage.metadata.work.at("document_bytes").first == document_bytes,
                    "MaxSim stage candidate or Work mismatch");
            if (kind == "read") {
                read_plan(stage, document);
                checked_requests += documents.at(index).plans.size();
            } else {
                auto& flop = documents.at(index).flop;
                flop = stage.metadata.work.at("dot_product_flop").first;
                require(flop && flop == derived.at("document_flop"), "MaxSim scoring Work mismatch");
                checked_flop = checked_add(checked_flop, flop);
            }
        } else {
            require((kind == "input" || kind == "topk") && kind == stage.key.op,
                    "unknown MaxSim stage");
        }
    }
    require(checked_requests == expected_reads && checked_flop == query_flop &&
                std::all_of(documents.begin(), documents.end(), [](const auto& d) {
                    return d.flop && !d.plans.empty();
                }), "MaxSim request/scoring coverage mismatch");

    rules.add(protocol, [this](const auto& m) -> timing::MessageSize {
        require(m.count == documents.size() && m.query == 0, "MaxSim message identity");
        if (m.kind == MessageKind::Execute)
            return {downlink, checked_add(downlink, command_header)};
        require(m.kind == MessageKind::Result, "unsupported MaxSim message");
        return {uplink, checked_add(uplink, result_header)};
    });
    for (auto kind : {MessageKind::Execute, MessageKind::Result}) {
        auto size = rules.size({protocol, kind, 0, 1, 1, 1, 1, count});
        // MQSim's default 128 B TLP and 28 B overhead, bounded before allocation.
        require(size.payload + 28 * ceil_div(size.payload, 128) <= INT32_MAX,
                "MaxSim protocol envelope exceeds link bounds");
    }

    const auto geometry = J::parse(config->effective_json());
    install_cost(c.at("compute"), integer(derived.at("document_flop")));
    buffer = std::make_unique<timing::BufferModel>(bridge, now, buffer_limit);
    require(!fs::exists(output), "trace output must be new");
    trace.open(output);
    emit({{"type", "configuration"}, {"placement", placement}, {"effective_config", c},
          {"effective_platform", platform}, {"device", geometry}, {"layout_sha256", layout->source_sha256()},
          {"device_sha256", config->source_sha256()}, {"package_sha256", ir.package_sha256},
          {"registry_sha256", ir.registry_sha256}, {"receipt_sha256", run.at("receipt_sha256")},
          {"mqsim_commit", mqsim_commit}, {"mqsim_source_sha256", mqsim_source_sha256},
          {"cost_profile_sha256", cost_profile->source_sha256()},
          {"mqsim_pcie_configured_GBps", g.lanes * g.lane_bandwidth},
          {"configured_unvalidated", true}});

    device = std::make_unique<OrdinaryDevice>(preload, *config, *layout);
    local = std::make_unique<timing::MetadataLocalReads>(
        *static_cast<SSD_Components::Host_Interface_NVMe*>(device->device->Host_interface), bridge, now);
    pcie = std::make_unique<timing::MetadataPcie>(*device->link, bridge, now, rules);
    executor = std::make_unique<dag::Executor>(*package, runtime, bridge, now, dag::Config{},
        [this](const auto& stage) { return lower(stage); });
    connect();

    auto* phy = dynamic_cast<SSD_Components::NVM_PHY_ONFI*>(device->device->PHY);
    require(phy && !active, "MaxSim requires one MQSim ONFI device");
    phy->ConnectToTransactionServicedSignal(&nand_completed);
    active = this;
}

void Run::State::install_cost(const J& compute, uint64_t flop) {
    const auto rate = integral_scale(compute.at(offload ? "device_effective_tflops" : "host_effective_tflops"), 1000);
    const auto launch = integral_scale(compute.at("batch_launch_us"), 1000);
    const auto max_docs = std::min<uint64_t>(batch_limit, documents.size());
    require(rate && flop <= UINT64_MAX / max_docs, "MaxSim batch model domain");
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
    J model = {{"operator", batch_operator}, {"operator_version", "1"}, {"stage_kind", stage.kind},
               {"context", batch_context}, {"location", offload ? "controller" : "host"},
               {"target_id", target.id}, {"work_units", stage.work_units},
               {"feature_ranges", J::array({{{"name", "dot_product_flop"}, {"unit", "flop"},
                                             {"minimum", flop}, {"maximum", max_docs * flop}}})},
               {"source", "configured"}, {"parameter_source", "MaxSim E0 effective compute configuration"},
               {"formula", "integer-rate/1"},
               {"parameter_schema_sha256", registry.parameter_schema_sha256("integer-rate/1")},
               {"parameters", {{"setup_ns", free ? 0 : launch}, {"units_per_ns", rate},
                               {"weights", J::array({free ? 0 : 1})}}}};
    J profile = {{"format", "ndpsim-cost-profile/1"}, {"fixture", true},
                 {"target", {{"id", target.id}, {"hardware", target.hardware}, {"os", target.os},
                             {"frequency", target.frequency}, {"affinity", target.affinity},
                             {"memory_bytes", target.memory_bytes}}},
                 {"rules", J::array({model})}};
    const auto raw = profile.dump();
    cost_profile = cost::Profile::parse(raw, capture::sha256(raw), registry, target, true);
    (void)demand(flop);
    (void)demand(max_docs * flop);
}

uint64_t Run::State::demand(uint64_t flop) const {
    cost::Input input;
    input.key = {0, "score-batch", "s0"};
    input.operator_id = batch_operator;
    input.operator_version = "1";
    input.stage_kind = "batch";
    input.context = batch_context;
    input.location = offload ? "controller" : "host";
    input.work = {{"dot_product_flop", {flop, "flop"}}};
    input.target = target;
    return cost_profile->demand(input);
}

void Run::State::read_plan(const dag::Stage& stage, const J& candidate) {
    const auto index = stage.metadata.features.at("candidate_index").first;
    auto& doc = documents.at(index);
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
    scoring.observations().enabled = false;
    buffer->observations().sink = [this](const auto& x) {
        emit({{"type", "input_buffer"}, {"time_ns", x.time}, {"candidate", std::stoull(x.lifetime)},
              {"credit", x.id}, {"bytes", x.bytes}, {"used", x.used}, {"reserve", x.reserve}});
    };
    staging.observations().sink = [this](const auto& x) {
        emit({{"type", "controller_staging"}, {"key", x.key}, {"submitted_ns", x.submitted},
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
        ++protocol_messages;
        emit({{"type", "protocol"}, {"kind", x.kind == MessageKind::Execute ? "command" : "result"},
              {"direction", x.direction == timing::Direction::H2D ? "H2D" : "D2H"},
              {"link_platform_id", x.id}, {"effective_payload_bytes", x.logical},
              {"header_bytes", x.payload - x.logical}, {"link_payload_bytes", x.payload},
              {"wire_bytes", x.wire}, {"submitted_ns", x.submitted}, {"arrived_ns", x.arrived}});
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
        emit({{"type", "pcie"}, {"event", event}, {"layer", layer},
              {"id", x.id}, {"platform_id", x.platform_id}, {"time_ns", x.time},
              {"direction", upload ? "H2D" : "D2H"}, {"payload_bytes", x.payload},
              {"wire_bytes", x.wire}, {"service_ns", x.service}});
    };
    executor->stages().sink = [this](const auto& x) {
        emit({{"type", "stage"}, {"op", x.key.op}, {"submitted_ns", x.submitted},
              {"visible_ns", x.completed}, {"success", x.success}});
    };
    executor->requests().sink = [this](const auto& x) {
        visible = x.visible;
        emit({{"type", "query"}, {"offered_ns", x.offered}, {"visible_ns", x.visible},
              {"drained_ns", x.drained}, {"success", x.success}});
    };
    observation.emit = [this](bool stat, const std::string& row) {
        emit({{"type", stat ? "nand_interval_union" : "nand_interval"}, {"mqsim", J::parse(row)}});
    };
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
    if (s.kind == "input" && offload) {
        op.duration_ns = command_ns;
        op.send_return = [this](Done done) {
            emit({{"type", "offload_command_service"}, {"duration_ns", command_ns}, {"end_ns", now()}});
            pcie->send({protocol, MessageKind::Execute, 0, 1, 1, 1, 1, documents.size()},
                [this, done = std::move(done)](const auto&) {
                    controller_input = downlink;
                    occupancy();
                    done({});
                });
        };
    } else if (s.kind == "read") {
        const auto index = s.features.at("candidate_index").first;
        op.input = [this, index](Done done) {
            auto& d = documents.at(index);
            require(!d.read_done && !d.submitted, "duplicate document read");
            d.read_done = std::move(done);
        };
    } else if (s.kind == "score") {
        const auto index = s.features.at("candidate_index").first;
        op.input = [this, index](Done done) {
            const auto& d = documents.at(index);
            require(!d.reads_left && !d.moves_left && d.credit && !scored.at(index) &&
                        ready.emplace(std::make_pair(now(), index), std::move(done)).second,
                    "document score queued before input or more than once");
            emit({{"type", "score_ready"}, {"candidate", index}, {"time_ns", now()}});
        };
        op.send_return = [this, index](Done done) {
            require(scored.at(index), "score before batch completion");
            if (!offload) {
                done({});
                return;
            }
            require(awaiting_return.emplace(index, std::move(done)).second, "duplicate score return");
            if (awaiting_return.size() == documents.size())
                send_result();
        };
    } else if (s.kind == "topk") {
        require(host_scores == uplink, "Top-K before all scores reached the Host");
        op.send_return = [this](Done done) {
            ++topk_count;
            emit({{"type", "topk"}, {"time_ns", now()}, {"descriptor_bytes", topk_bytes}});
            host_scores = 0;
            occupancy();
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
        emit({{"type", "measurement_start"}, {"time_ns", origin}, {"preload_sectors", preload},
              {"preload_pages", preload / config->geometry().page_sectors}});
        executor->start([this](auto result) {
            require(!result.failure, result.failure ? result.failure->detail.c_str() : "");
            finished = true;
        });
    }
    // Settle every continuation at this SST timestamp before choosing a batch.
    // MetadataLocalReads may have just moved backend completions into the bridge.
    if (!launched || bridge.pending_now())
        return;
    if (finished) {
        if (bridge.idle() && !reported)
            report();
        return;
    }
    pump_scores();
    pump_reads();
}

void Run::State::pump_reads() {
    while (next_document < documents.size()) {
        const auto index = next_document;
        auto& d = documents.at(index);
        if (!d.read_done || reserving)
            return;
        const bool new_document = !d.credit;
        const char* blocked = qd == qd_limit ? "qd"
                             : new_document && window == window_limit ? "window"
                             : new_document && document_bytes > buffer_limit - buffer->used() ? "buffer"
                             : nullptr;
        if (blocked) {
            if (last_block != blocked)
                emit({{"type", "backpressure"}, {"reason", blocked}, {"time_ns", now()},
                      {"candidate", index}, {"outstanding_reads", qd},
                      {"window_documents", window}, {"input_buffer_bytes", buffer->used()}});
            last_block = blocked;
            return;
        }
        last_block.clear();
        if (new_document) {
            reserving = true;
            buffer->reserve(document_bytes, std::to_string(index), [this, index](auto credit) {
                documents.at(index).credit = credit;
                reserving = false;
                peak_window = std::max(peak_window, ++window);
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
        emit({{"type", "read_submit"}, {"candidate", index}, {"document_id", d.id}, {"slice", slice},
              {"lba", request.lba}, {"sectors", request.sectors}, {"time_ns", now()},
              {"valid_bytes", plan.logical_bytes}, {"outstanding_reads", qd},
              {"path", offload ? "local" : "ordinary"}});
        if (offload) {
            local->submit(plan, d.key, [this, index, slice](auto completion) {
                require(completion.requests.size() == 1, "local request unexpectedly split");
                complete_read(index, slice);
            });
        } else {
            Simulator->Synchronize_now(now());
            auto token = device->window->read(request.lba, request.sectors);
            require(ordinary.emplace(token, OrdinaryRead{index, slice}).second, "duplicate ordinary token");
        }
        if (d.submitted == d.plans.size())
            ++next_document;
    }
}

void Run::State::complete_read(size_t index, size_t slice) {
    auto& d = documents.at(index);
    require(qd && d.reads_left && d.credit, "read completion without in-flight resources");
    --qd;
    ++completed_reads;
    const auto& plan = d.plans.at(slice);
    read_bytes += plan.sector_bytes;
    effective_bytes += plan.logical_bytes;
    emit({{"type", "read_complete"}, {"candidate", index}, {"slice", slice}, {"time_ns", now()},
          {"outstanding_reads", qd}});
    if (!--d.reads_left) {
        require(window > 0, "document read-window underflow");
        --window;
        emit({{"type", "read_window"}, {"candidate", index}, {"time_ns", now()},
              {"used", window}, {"reserve", false}});
    }
    if (offload) {
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

void Run::State::pump_scores() {
    if (scoring.pending() || ready.empty())
        return;
    std::vector<size_t> members;
    std::vector<Done> completions;
    uint64_t flop = 0;
    for (auto it = ready.begin(); it != ready.end() && members.size() < batch_limit; it = ready.erase(it)) {
        const auto index = it->first.second;
        members.push_back(index);
        completions.push_back(std::move(it->second));
        flop = checked_add(flop, documents.at(index).flop);
    }
    const auto duration = demand(flop);
    const auto batch = ++batches;
    flops = checked_add(flops, flop);
    compute_ns = checked_add(compute_ns, duration);
    scoring.submit(std::to_string(batch), duration, [this, batch, members, flop, duration] {
        emit({{"type", "score_batch"}, {"batch", batch}, {"candidates", members},
              {"flop", flop}, {"start_ns", now()}, {"end_ns", checked_add(now(), duration)},
              {"duration_ns", duration}});
    }, [this, members, completions = std::move(completions)](bool ok) {
        require(ok, "MaxSim scoring failed");
        for (size_t i = 0; i < members.size(); ++i) {
            const auto index = members.at(i);
            auto& d = documents.at(index);
            require(!scored.at(index) && d.credit, "duplicate scoring or missing buffer credit");
            scored.at(index) = true;
            buffer->release(d.credit);
            d.credit = 0;
            if (offload)
                controller_scores += score_bytes;
            else
                host_scores += score_bytes;
            completions.at(i)({});
        }
        occupancy();
    });
}

void Run::State::send_result() {
    require(controller_scores == uplink && controller_input == downlink, "incomplete controller scores");
    controller_input = 0;
    occupancy();
    pcie->send({protocol, MessageKind::Result, 0, 1, 1, 1, 1, documents.size()}, [this](const auto&) {
        host_scores = controller_scores;
        controller_scores = 0;
        occupancy();
        auto completions = std::move(awaiting_return);
        awaiting_return.clear();
        for (const auto& entry : completions)
            entry.second({});
    });
}

void Run::State::occupancy() {
    peak_controller_input = std::max(peak_controller_input, controller_input);
    peak_controller_scores = std::max(peak_controller_scores, controller_scores);
    peak_host_scores = std::max(peak_host_scores, host_scores);
    emit({{"type", "metadata_buffers"}, {"time_ns", now()}, {"controller_query_ids_bytes", controller_input},
          {"controller_score_bytes", controller_scores}, {"host_score_bytes", host_scores}});
}

void Run::State::verify() const {
    require(finished && bridge.idle() && executor->drained() && !runtime.task_records() &&
                !runtime.owners() && !runtime.pins() && !host.pending() && !scoring.pending() &&
                !staging.pending() && ready.empty() && awaiting_return.empty(),
            "MaxSim DAG, tokens or services not drained");
    require(!qd && !window && !reserving && !buffer->used() && !buffer->waiting() && !buffer->live() &&
                ordinary.empty() && !device->flow->pending() && !local->pending() &&
                !local->pending_requests() && !pcie->pending() && !Simulator->Peek_next_time() &&
                !controller_scores && !controller_input && !host_scores,
            "MaxSim reads, messages or credit not drained");
    require(completed_reads == expected_reads && submitted_reads == completed_reads &&
                next_document == documents.size() && flops == query_flop && topk_count == 1 &&
                std::all_of(scored.begin(), scored.end(), [](bool done) { return done; }) &&
                visible >= origin && now() >= visible && Simulator->Time() <= now(),
            "MaxSim terminal or Work accounting disagreement");
    require(offload ? (dma == 0 && staged_bytes == effective_bytes && protocol_messages == 2)
                    : (dma == read_bytes && staged_bytes == 0 && protocol_messages == 0),
            "MaxSim placement or protocol ledger mismatch");
}

void Run::State::report() {
    verify();
    observation.report(origin, now());
    J link = J::object();
    for (const auto& [direction, total] : link_ledger)
        link[direction] = {{"messages", total.messages}, {"payload_bytes", total.payload},
                           {"wire_bytes", total.wire}, {"service_ns", total.service}};
    emit({{"type", "summary"}, {"success", true}, {"scope", "synthetic_fixture_execution"},
          {"configured_unvalidated", true}, {"placement", placement},
          {"origin_ns", origin}, {"visible_ns", visible}, {"drained_ns", now()},
          {"query_latency_ns", visible - origin}, {"logical_reads", completed_reads},
          {"ordinary_nvme_reads", offload ? 0 : completed_reads}, {"mqsim_local_reads", offload ? completed_reads : 0},
          {"logical_request_bytes", read_bytes}, {"effective_document_bytes", effective_bytes},
          {"document_d2h_payload_bytes", dma}, {"nand_read_transactions", nand_reads},
          {"nand_read_bytes", nand_read_bytes}, {"nand_user_read_bytes", nand_user_read_bytes},
          {"nand_read_amplification", double(nand_read_bytes) / effective_bytes},
          {"controller_staging_bytes", staged_bytes}, {"scores", documents.size()}, {"topk_count", topk_count},
          {"topk_bytes", topk_bytes}, {"flop", flops}, {"batches", batches}, {"compute_ns", compute_ns},
          {"peak_outstanding_reads", peak_qd}, {"peak_read_window_documents", peak_window},
          {"peak_input_buffer_bytes", buffer->peak()}, {"peak_scoring_engines", scoring.peak_busy()},
          {"peak_controller_query_ids_bytes", peak_controller_input},
          {"peak_controller_score_bytes", peak_controller_scores}, {"peak_host_score_bytes", peak_host_scores},
          {"protocol_messages", protocol_messages}, {"pcie", link}, {"all_resources_drained", true}});
    trace.flush();
    require(bool(trace), "cannot flush MaxSim trace");
    reported = true;
}

Run::Run(const fs::path& path, EventBridge& bridge, EventBridge::Clock now)
    : state_(std::make_unique<State>(path, bridge, std::move(now))) {}
Run::~Run() = default;
void Run::start() {
    state_->device->start();
    state_->bridge.start();
}
void Run::poll() { state_->poll(); }
bool Run::finished() const { return state_->reported; }
void Run::verify() const {
    require(state_->reported, "MaxSim summary missing");
    state_->verify();
}
} // namespace maxsim
