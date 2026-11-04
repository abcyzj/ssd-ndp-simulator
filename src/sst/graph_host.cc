#include <sst/core/sst_config.h>
#include <sst/core/component.h>
#include <sst/core/link.h>
#include "backend/ordinary_device.h"
#include "search/graph.h"
#include "search/ir_graph.h"
#include "search/replay.h"
#include "ir/mapping.h"
#include "model/controller.h"
#include "backend/observation.h"
#include "observation/output.h"
#include <sstream>
#include <fstream>
#include <iomanip>
#include <cstring>
#include <chrono>
namespace ndpsim {
namespace {
content::Bytes load(const std::string& path, size_t expected) {
    std::ifstream f(path, std::ios::binary);
    if (!f)
        throw std::invalid_argument("cannot open " + path);
    f.seekg(0, std::ios::end);
    if (f.tellg() < 0 || uint64_t(f.tellg()) != expected)
        throw std::invalid_argument("bad file size " + path);
    f.seekg(0);
    content::Bytes b(expected);
    f.read(reinterpret_cast<char*>(b.data()), b.size());
    if (!f)
        throw std::invalid_argument("cannot read " + path);
    if (b.size() != expected)
        throw std::invalid_argument("bad file size " + path);
    return b;
}
void score_json(std::ostream& o, const std::vector<operators::Score>& v) {
    o << '[';
    bool first = true;
    for (auto s : v) {
        if (!first)
            o << ',';
        first = false;
        o << "{\"id\":" << s.id << ",\"distance\":" << std::setprecision(17) << s.distance << '}';
    }
    o << ']';
}
} // namespace
class GraphWake : public SST::Event {
public:
    explicit GraphWake(uint64_t g) : generation(g) {
    }
    uint64_t generation;
};
class GraphHost : public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(GraphHost,
                               "ndpsim",
                               "GraphHost",
                               SST_ELI_ELEMENT_VERSION(0, 1, 0),
                               "Deterministic host graph search",
                               COMPONENT_CATEGORY_UNCATEGORIZED)
    SST_ELI_DOCUMENT_PARAMS({"execution_engine", "direct/ir/replay", "ir"},
                            {"data_provider", "ssd/host_memory", "ssd"},
                            {"replay_config", "preflight snapshot and bound identity", ""},
                            {"native_workers", "replay workers", "1"},
                            {"cpu_profile", "CPU Profile path for static replay", ""},
                            {"ir_contract", "validated run contract", ""},
                            {"placement", "host/controller", "host"},
                            {"content_backend", "mmap/memory", "mmap"},
                            {"device_profile", "controlled device profile", "native-small"},
                            {"resource_budget_bytes",
                             "metadata admission budget bytes",
                             "4294967296"},
                            {"capacity_bytes", "independent controller buffer B", "16384"},
                            {"staging_bytes_per_ns", "buffer B/ns", "16"},
                            {"controller_slots", "arithmetic slots", "1"},
                            {"launch_ns", "arithmetic setup ns", "1"},
                            {"flops_per_ns", "arithmetic FLOPs/ns", "16"},
                            {"scratch_bytes", "extra block bytes", "0"},
                            {"vector_sector_bound", "vector sector coverage bytes", "512"},
                            {"max_inflight", "finite query admission limit", "1"},
                            {"semantic_output", "optional reference semantics JSONL", ""},
                            {"event_log", "enable JSONL events", "0"},
                            {"output_budget_bytes", "required+detail byte budget", "2147483648"},
                            {"records", "mandatory result records", ""},
                            {"data", "Validated dataset directory", ""},
                            {"vector_file", "Validated FP32 base file", ""},
                            {"query_file", "Validated FP32 query file", ""},
                            {"graph_file", "Validated fixed-slot graph file", ""},
                            {"output", "JSONL output file", ""},
                            {"count", "N", "2048"},
                            {"dimensions", "d", "32"},
                            {"degree", "R", "16"},
                            {"queries", "query count", "32"},
                            {"entry", "entry node", "0"},
                            {"k", "k", "10"},
                            {"width", "L", "32"},
                            {"batch", "B", "1"},
                            {"budget", "E", "64"},
                            {"chunk", "score chunk", "16"},
                            {"slots", "host CPU slots", "1"},
                            {"distance_setup", "ns", "3"},
                            {"distance_rate", "FLOPs/ns", "4"},
                            {"control_setup", "ns", "1"},
                            {"control_rate", "units/ns", "1"},
                            {"check_units", "units", "1"},
                            {"comparison_units", "units", "1"})
    GraphHost(SST::ComponentId_t id, SST::Params& p) : SST::Component(id) {
        if (getNumRanks().rank != 1 || getNumRanks().thread != 1)
            throw std::invalid_argument("graph requires one rank/thread");
        auto engine = p.find<std::string>("execution_engine", "ir");
        replay_mode_ = engine == "replay";
        if (!replay_mode_ && p.contains("cpu_profile"))
            throw std::invalid_argument("CPU Profile requires the static replay entry point");
        if (engine != "direct" && engine != "ir" && !replay_mode_)
            throw std::invalid_argument("Unsupported execution_engine");
        ir_mode_ = engine == "ir";
        controller_mode_ = p.find<std::string>("placement", "host") == "controller";
        if (p.find<std::string>("placement", "host") != "host" && !controller_mode_)
            throw std::invalid_argument("Unsupported placement");
        cfg_ = {p.find<uint32_t>("count", 2048),
                p.find<uint32_t>("entry", 0),
                p.find<uint32_t>("k", 10),
                p.find<uint32_t>("width", 32),
                p.find<uint32_t>("batch", 1),
                p.find<uint32_t>("budget", 64),
                p.find<uint32_t>("chunk", 16)};
        search::validate(cfg_);
        max_inflight_ = p.find<uint32_t>("max_inflight", 1);
        if (!max_inflight_)
            throw std::invalid_argument("max_inflight must be positive");
        d_ = p.find<uint32_t>("dimensions", 32);
        r_ = p.find<uint32_t>("degree", 16);
        nq_ = p.find<uint32_t>("queries", 32);
        if (!d_ || !nq_ || r_ >= cfg_.count)
            throw std::invalid_argument("invalid dimensions/queries/degree");
        const auto provider = p.find<std::string>("data_provider", "ssd");
        if (replay_mode_ ? provider != "host_memory" || controller_mode_ ||
                               p.find<uint64_t>("native_workers", 1) != 1 ||
                               p.find<uint64_t>("slots", 1) != 1 || max_inflight_ != 1
                         : provider != "ssd" || p.contains("replay_config"))
            throw std::invalid_argument(
                "Unsupported replay provider/placement/worker/slot combination");
        ir::Json replay_config;
        if (replay_mode_) {
            replay_config = ir::read_json(p.find<std::string>("replay_config", ""));
            const auto package = replay_config.at("package").get<std::string>();
            replay_index_.open(package + "/index.jsonl", std::ios::binary);
            replay_ops_.open(package + "/operations.jsonl", std::ios::binary);
            if (!replay_index_ || !replay_ops_)
                throw std::invalid_argument("cannot open preflight replay streams");
        }
        if (ir_mode_) {
            ir_contract_ = ir::parse(ir::read_json(p.find<std::string>("ir_contract", "")));
            const auto& m = ir_contract_.manifest;
            ir::Json expected = {{"strategy", "ndpsim-ranked-expansion-v1"},
                                 {"entry", cfg_.entry},
                                 {"k", cfg_.k},
                                 {"L", cfg_.width},
                                 {"B", cfg_.batch},
                                 {"E", cfg_.budget},
                                 {"chunk", cfg_.chunk}};
            if (!ir_contract_.operations.empty() || m.search != expected ||
                m.mapping.at("placement") != (controller_mode_ ? "controller" : "host") ||
                m.artifacts.at("dataset").at("shape") != ir::Json::array({cfg_.count, d_}) ||
                m.artifacts.at("queries").at("shape") != ir::Json::array({nq_, d_}) ||
                m.artifacts.at("index").at("shape") != ir::Json::array({cfg_.count, r_}))
                throw std::invalid_argument("IR contract differs from admitted run parameters");
        }
        auto dir = p.find<std::string>("data", "");
        const auto* profile =
            replay_mode_ ? nullptr
                         : &device_profile(p.find<std::string>("device_profile", "native-small"));
        auto resource_budget =
            p.find<uint64_t>("resource_budget_bytes", uint64_t(4) * 1024 * 1024 * 1024);
        uint64_t preload = 0;
        if (profile) {
            // Checked layout and admission before mapping content or creating MQSim metadata.
            auto shape = content::ContentStore(std::vector<content::Input>{});
            ann_ = content::ann_layout(shape, 1, 1, cfg_.count, d_, r_, 0, true);
            preload = model::checked_mul(
                model::checked_add(ann_.end_lba, profile->page_sectors - 1) / profile->page_sectors,
                profile->page_sectors);
            if (preload > profile->raw_sectors || profile->metadata_estimate > resource_budget)
                throw std::invalid_argument("profile capacity/resource preflight failed");
        }
        model::ControllerConfig cc;
        cc.dimension = d_;
        cc.chunk_limit = cfg_.chunk;
        cc.max_inflight = max_inflight_;
        cc.capacity_bytes = p.find<uint64_t>("capacity_bytes", 16384);
        capacity_ = cc.capacity_bytes;
        cc.staging_bytes_per_ns = p.find<uint64_t>("staging_bytes_per_ns", 16);
        cc.slots = p.find<uint64_t>("controller_slots", 1);
        cc.launch_ns = p.find<uint64_t>("launch_ns", 1);
        cc.flops_per_ns = p.find<uint64_t>("flops_per_ns", 16);
        cc.scratch_bytes = p.find<uint64_t>("scratch_bytes", 0);
        cc.vector_sector_bound = p.find<uint64_t>("vector_sector_bound", 512);
        if (controller_mode_) {
            if (cc.vector_sector_bound < ann_.vector_stride)
                throw std::invalid_argument(
                    "vector sector bound smaller than actual layout coverage");
            model::Controller::bounds(cc);
        }
        auto vector_file = p.find<std::string>("vector_file", dir + "/vectors.f32");
        auto graph_file = p.find<std::string>("graph_file", dir + "/adjacency.u32");
        queries_ = load(p.find<std::string>("query_file", dir + "/queries.f32"),
                        model::checked_mul(model::checked_mul(nq_, d_), 4));
        auto backend = p.find<std::string>("content_backend", "mmap");
        auto check_input = [&](const char* name, const content::Bytes& bytes) {
            if (replay_mode_ && content::sha256(bytes) != replay_config.at("objects").at(name))
                throw std::invalid_argument(std::string("replay input hash mismatch: ") + name);
        };
        check_input("queries", queries_);
        if (backend == "mmap") {
            store_ = std::make_unique<content::ContentStore>(
                vector_file, graph_file, cfg_.count, d_, r_);
            if (replay_mode_)
                store_->require_file_hashes(replay_config.at("objects").at("base"),
                                            replay_config.at("objects").at("graph"));
        } else if (backend == "memory") {
            auto vb = load(vector_file, uint64_t(cfg_.count) * d_ * 4);
            auto ab = load(graph_file, uint64_t(cfg_.count) * (uint64_t(r_) + 1) * 4);
            check_input("base", vb);
            check_input("graph", ab);
            std::vector<content::Input> objects;
            for (uint32_t i = 0; i < cfg_.count; ++i) {
                auto v = vb.begin() + uint64_t(i) * d_ * 4;
                auto a = ab.begin() + uint64_t(i) * (r_ + uint64_t(1)) * 4;
                content::Bytes adjacency(a, a + (r_ + uint64_t(1)) * 4);
                auto ai = search::ids(adjacency);
                if (ai[0] > r_)
                    throw std::invalid_argument("degree exceeds R");
                for (size_t j = 1; j <= ai[0]; ++j)
                    if (ai[j] >= cfg_.count)
                        throw std::invalid_argument("invalid graph ID");
                // Validate finite FP32 using the same numerical input contract, without retaining host scores.
                content::Bytes vector(v, v + uint64_t(d_) * 4);
                operators::squared_l2(vector, {}, {});
                objects.push_back({{1, content::Kind::Vector, i, 1}, std::move(vector)});
                adjacency.resize((uint64_t(ai[0]) + 1) * 4);
                objects.push_back({{1, content::Kind::Adjacency, i, 1}, std::move(adjacency)});
            }
            store_ = std::make_unique<content::ContentStore>(std::move(objects));
        } else
            throw std::invalid_argument("Unsupported content backend");
        handles_ = std::make_unique<content::Handles>(*store_);
        if (!replay_mode_) {
            layout_ = std::make_unique<content::Layout>(*store_, ann_.end_lba, ann_, cfg_.count);
            device_ = std::make_unique<OrdinaryDevice>(preload, profile->name, resource_budget);
        }
        output_budget_.limit = p.find<uint64_t>("output_budget_bytes", uint64_t(2) << 30);
        out_.exceptions(std::ios::badbit);
        auto semantic_path = p.find<std::string>("semantic_output", "");
        if (!semantic_path.empty()) {
            semantic_.open(semantic_path);
            if (!semantic_)
                throw std::invalid_argument("cannot open reference semantic log");
        }
        records_.open(p.find<std::string>("records", ""));
        if (!records_)
            throw std::invalid_argument("cannot open graph records");
        if (p.find<unsigned>("event_log", 0)) {
            log_.open(p.find<std::string>("output", ""));
            if (!log_)
                throw std::invalid_argument("cannot open graph log");
            out_.rdbuf(log_.rdbuf());
        }
        if (device_)
            records_ << "{\"type\":\"device_profile\",\"name\":\"" << profile->name
                     << "\",\"xml_sha256\":\"" << profile->xml_sha256
                     << "\",\"namespace_sectors\":" << device_->namespace_sectors
                     << ",\"raw_sectors\":" << profile->raw_sectors
                     << ",\"page_sectors\":" << profile->page_sectors
                     << ",\"preload_sectors\":" << preload << "}\n";
        observation_ = std::make_unique<Observation>(false);
        link_ = configureSelfLink(
            "wake", "1ns", new SST::Event::Handler<GraphHost, &GraphHost::wake>(this));
        auto now = [this] {
            return getCurrentSimTimeNano();
        };
        bridge_ = std::make_unique<EventBridge>(
            now,
            [this](auto delay, auto g) {
                ++pending_;
                link_->send(delay, new GraphWake(g));
            },
            100000,
            replay_mode_ ? EventBridge::BackendMode::PlatformOnly
                         : EventBridge::BackendMode::MQSim);
        rt_ = std::make_unique<runtime::Runtime>(*bridge_, now);
        model::HostConfig c;
        c.slots = p.find<uint64_t>("slots", 1);
        c.distance = {p.find<uint64_t>("distance_setup", 3), p.find<uint64_t>("distance_rate", 4)};
        c.control = {p.find<uint64_t>("control_setup", 1), p.find<uint64_t>("control_rate", 1)};
        c.check_units = p.find<uint64_t>("check_units", 1);
        c.comparison_units = p.find<uint64_t>("comparison_units", 1);
        if (replay_mode_) {
            const auto& a = replay_config.at("applicability");
            const auto& b = a.at("build");
            model::CpuApplicability actual{a.at("hardware"),
                                           a.at("os"),
                                           a.at("memory_bytes"),
                                           a.at("frequency"),
                                           a.at("affinity"),
                                           b.at("source_sha256"),
                                           b.at("binary_sha256"),
                                           b.at("compiler"),
                                           b.at("flags").get<std::vector<std::string>>(),
                                           d_,
                                           cfg_.count,
                                           "resident_random",
                                           1};
            c.cpu_profile = std::make_shared<model::CpuProfile>(
                model::CpuProfile::load(p.find<std::string>("cpu_profile", ""),
                                        replay_config.at("profile_sha256"),
                                        actual,
                                        replay_config.at("allow_fixture")));
        }
        cpu_ = std::make_unique<model::HostCpu>(*rt_, *bridge_, now, c);
        if (device_) {
            device_->flow->completed = [this](auto c) {
                if (c.write)
                    return;
                auto it = requests_.find(c.token);
                if (it == requests_.end())
                    throw std::logic_error("unknown graph read completion");
                auto read = it->second;
                requests_.erase(it);
                read->association->complete(c.token, c.lba, c.sectors, c.write);
                out_ << "{\"type\":\"read_complete\",\"query\":" << read->query
                     << ",\"task\":" << read->task << ",\"request\":" << c.token
                     << ",\"lba\":" << c.lba << ",\"sectors\":" << c.sectors
                     << ",\"time_ns\":" << c.time << "}\n";
                if (read->association->complete()) {
                    handles_->publish(read->content);
                    rt_->start(read->task);
                }
            };
            device_->root->data_arrived = [this](auto address, auto bytes) {
                if (measuring_)
                    dma_ += bytes;
                device_->flow->data(address, bytes);
            };
            device_->link->observe = [this](const char* phase,
                                            const Host_Components::PCIe_Link::Transfer& t) {
                if (!measuring_)
                    return;
                if (std::string(phase) == "arrive") {
                    auto& v = link_totals_[unsigned(t.destination)];
                    ++v[0];
                    v[1] += t.payload;
                    v[2] += t.wire;
                    v[3] += t.service;
                }
                out_ << "{\"type\":\"link\",\"phase\":\"" << phase << "\",\"id\":" << t.id
                     << ",\"platform_id\":" << t.platform_id
                     << ",\"message_type\":" << unsigned(t.type)
                     << ",\"destination\":" << unsigned(t.destination)
                     << ",\"payload\":" << t.payload << ",\"wire\":" << t.wire
                     << ",\"service_ns\":" << t.service << ",\"time_ns\":" << t.time << "}\n";
            };
        }
        if (controller_mode_) {
            auto* host =
                static_cast<SSD_Components::Host_Interface_NVMe*>(device_->device->Host_interface);
            local_ = std::make_unique<LocalReads>(*host, *bridge_, now);
            local_->observe = [this](const LocalReads::Completion& c) {
                for (auto& r : c.requests) {
                    out_ << "{\"type\":\"local_request\",\"query\":" << c.context.query
                         << ",\"task\":" << c.context.task << ",\"request\":" << r.token
                         << ",\"lba\":" << r.lba << ",\"sectors\":" << r.sectors
                         << ",\"submitted_ns\":" << r.submitted << ",\"completed_ns\":" << r.time
                         << ",\"transactions\":[";
                    bool first = true;
                    for (auto& t : r.transactions) {
                        if (!first)
                            out_ << ',';
                        first = false;
                        out_ << "{\"id\":" << t.id << ",\"bytes\":" << t.bytes
                             << ",\"time_ns\":" << t.time << "}";
                    }
                    out_ << "]}\n";
                }
            };
            transport_ = std::make_unique<transport::PcieTransport>(*device_->link, *bridge_, now);
            controller_ = std::make_unique<model::Controller>(
                *rt_, *handles_, *layout_, *local_, *transport_, *bridge_, now, cc);
        }
        if (ir_mode_) {
            executor_ = std::make_unique<ir::Executor>(ir_contract_, *rt_, now);
            if (log_.is_open()) {
                executor_->observe = [this](const ir::Operation& op,
                                            const char* phase,
                                            uint64_t task,
                                            const ir::Values& values) {
                    if (controller_ && op.op == ir::Operator::ScoreL2 &&
                        std::string(phase) == "visible")
                        out_ << ir::Json({{"type", "score_visible"},
                                          {"query", op.scope.query},
                                          {"time_ns", getCurrentSimTimeNano()},
                                          {"scores", values.at("scores").data()}})
                                    .dump()
                             << '\n';
                    ir::Json summaries = ir::Json::array();
                    for (const auto& [port, value] : values) {
                        auto text = value.state ? value.state->data().dump() : value.data().dump();
                        uint64_t hash = 14695981039346656037ULL;
                        for (unsigned char b : text) {
                            hash ^= b;
                            hash *= 1099511628211ULL;
                        }
                        summaries.push_back(
                            {{"port", port}, {"serialized_bytes", text.size()}, {"fnv1a64", hash}});
                    }
                    ir::Document d = ir_contract_;
                    d.operations = {op};
                    out_ << ir::Json({{"type", "ir_operation"},
                                      {"schema", "ndpsim-ir-diagnostics/1"},
                                      {"phase", phase},
                                      {"time_ns", getCurrentSimTimeNano()},
                                      {"task", task},
                                      {"values", summaries},
                                      {"operation", ir::encode(d).at("operations").at(0)}})
                                .dump()
                         << '\n';
                };
            }
            mapping_ = std::make_unique<ir::Mapping>(
                ir_contract_,
                *executor_,
                *rt_,
                *cpu_,
                *store_,
                *layout_,
                [this](auto q, const content::Selection& selection) {
                    return read(q, selection.object.kind, selection.object.id);
                },
                std::vector<ir::Mapping::ObjectBinding>{{"base", 1, 1, content::Kind::Vector},
                                                        {"graph", 1, 1, content::Kind::Adjacency}},
                controller_.get(),
                log_.is_open() ? ir::Mapping::Bind([this](auto op, auto task) {
                    out_ << ir::Json({{"type", "ir_binding"}, {"op", op}, {"task", task}}).dump()
                         << '\n';
                })
                               : ir::Mapping::Bind{});
            if (log_.is_open())
                mapping_->session_bind = [this](auto q, const char* role, auto task) {
                    out_ << ir::Json({{"type", "ir_session"},
                                      {"query", q},
                                      {"role", role},
                                      {"task", task}})
                                .dump()
                         << '\n';
                };
            if (controller_ && log_.is_open())
                controller_->bind_chunk = [this](auto parent, auto child) {
                    out_ << ir::Json({{"type", "ir_task_edge"},
                                      {"parent_task", parent},
                                      {"task", child}})
                                .dump()
                         << '\n';
                };
        }
        if (ir_mode_ && log_.is_open())
            rt_->observations().sink = [this](const auto& t) {
                out_ << ir::Json({{"type", "ir_runtime"},
                                  {"query", t.query},
                                  {"task", t.task},
                                  {"time_ns", t.time_ns},
                                  {"state", static_cast<unsigned>(t.state)}})
                            .dump()
                     << '\n';
            };
        rt_->statistics_only();
        handles_->statistics_only();
        if (semantic_.is_open()) {
            cpu_->observe_score = [this](auto query, const auto& result) {
                semantic_score(query, 0, result);
            };
            if (controller_)
                controller_->observe_score = [this](auto query, auto chunk, const auto& result) {
                    semantic_score(query, chunk, result);
                };
        }
        cpu_->observations().sink = [this](const auto& s) {
            if (semantic_.is_open() && s.kind == model::Kind::Control)
                semantic_ << "{\"type\":\"control\",\"query\":" << s.query
                          << ",\"work\":" << semantic_work(s.work) << "}\n";
            records_ << "{\"type\":\"service\",\"task\":" << s.task << ",\"query\":" << s.query
                     << ",\"ready_ns\":" << s.ready_ns << ",\"read_bytes\":" << s.work.read_bytes
                     << ",\"write_bytes\":" << s.work.write_bytes << ",\"kind\":\""
                     << (s.kind == model::Kind::Distance ? "distance" : "control")
                     << "\",\"start_ns\":" << s.start_ns << ",\"submitted_ns\":" << s.submitted_ns
                     << ",\"end_ns\":" << s.end_ns << ",\"flops\":" << s.work.flops
                     << ",\"checks\":" << s.work.candidate_checks
                     << ",\"comparisons\":" << s.work.comparisons << "}\n";
        };
        if (controller_) {
            controller_->observations().sink = [this](const auto& r) {
                records_ << "{\"type\":\"local_storage\",\"query\":" << r.query
                         << ",\"task\":" << r.task << ",\"submitted_ns\":" << r.submitted
                         << ",\"completed_ns\":" << r.completed
                         << ",\"sector_bytes\":" << r.sector_bytes << ",\"requests\":" << r.requests
                         << ",\"transactions\":" << r.transactions << "}\n";
            };
            transport_->observations().sink = [this](const auto& r) {
                records_ << "{\"type\":\"message\",\"id\":" << r.id
                         << ",\"kind\":" << unsigned(r.kind) << ",\"query\":" << r.query
                         << ",\"task\":" << r.task << ",\"payload\":" << r.payload
                         << ",\"useful\":" << r.useful << ",\"submitted_ns\":" << r.submitted
                         << ",\"arrived_ns\":" << r.arrived << "}\n";
            };
            controller_->buffer().observations().sink = [this](const auto& r) {
                records_ << "{\"type\":\"credit\",\"time_ns\":" << r.time << ",\"id\":" << r.id
                         << ",\"query\":" << r.query << ",\"task\":" << r.task
                         << ",\"bytes\":" << r.bytes << ",\"used\":" << r.used
                         << ",\"reserve\":" << (r.reserve ? "true" : "false") << "}\n";
            };
            for (auto* resource : {&controller_->port(), &controller_->compute()})
                resource->observations().sink = [this](const auto& r) {
                    records_ << "{\"type\":\"controller_service\",\"stage\":\"" << r.stage
                             << "\",\"query\":" << r.query << ",\"task\":" << r.task
                             << ",\"submitted_ns\":" << r.submitted << ",\"start_ns\":" << r.start
                             << ",\"end_ns\":" << r.end << ",\"bytes\":" << r.bytes << "}\n";
                };
        }
        registerAsPrimaryComponent();
        primaryComponentDoNotEndSim();
    }
    void setup() override {
        if (device_)
            device_->start();
        bridge_->start();
        if (replay_mode_)
            bridge_->enqueue(
                getCurrentSimTimeNano(), EventBridge::ContinueSubmit, "resident start", [this] {
                    measuring_ = true;
                    origin_ = getCurrentSimTimeNano();
                    query_wall_start_ = std::chrono::steady_clock::now();
                    next();
                });
    }
    void finish() override {
        auto wall_end = std::chrono::steady_clock::now();
        auto seconds = [](auto duration) {
            return std::chrono::duration<double>(duration).count();
        };
        std::printf("NDPSIM_EFFICIENCY "
                    "{\"initialization_wall_seconds\":%.9g,\"query_wall_seconds\":%.9g}\n",
                    seconds(query_wall_start_ - wall_start_),
                    seconds(wall_end - query_wall_start_));
        store_->verify_identity();
        if ((replay_error_.empty() && completed_ != nq_) || active_ || !requests_.empty() ||
            cpu_->busy() || rt_->live_results() || handles_->live_records())
            throw std::logic_error("incomplete graph run/leaked handles");
        if (replay_session_ || (replay_mode_ && (rt_->task_records() || rt_->wait_records())))
            throw std::logic_error("replay resources leaked");
        if (!replay_error_.empty()) {
            records_ << ir::Json({{"type", "replay_failure"},
                                  {"detail", replay_error_},
                                  {"drained", true}})
                            .dump()
                     << '\n';
            records_.flush();
            if (log_.is_open())
                log_.flush();
            getSimulationOutput().fatal(CALL_INFO, 1, "%s\n", replay_error_.c_str());
        }
        if (executor_ && (executor_->counts().active_ops || executor_->counts().active_values ||
                          mapping_->sessions() || !ir_sessions_.empty()))
            throw std::logic_error("IR graph run leaked active operations/values/sessions");
        if (executor_) {
            auto c = executor_->counts();
            ir::Json counts = ir::Json::array();
            for (const auto& [op, n] : executor_->operator_counts)
                counts.push_back({{"operator",
                                   ir::capabilities()
                                       .at("operators")
                                       .at(static_cast<unsigned>(op))
                                       .at("operator")},
                                  {"submitted", n.submitted},
                                  {"completed", n.completed},
                                  {"failed", n.failed}});
            records_ << ir::Json({{"type", "ir_summary"},
                                  {"submitted", c.submitted},
                                  {"completed", c.completed},
                                  {"failed", c.failed},
                                  {"active_ops", c.active_ops},
                                  {"peak_ops", c.peak_ops},
                                  {"active_values", c.active_values},
                                  {"peak_values", c.peak_values},
                                  {"operators", counts}})
                            .dump()
                     << '\n';
        }
        // Mandatory records are independent of the optional detailed event stream.
        out_.rdbuf(records_.rdbuf());
        observation_->emit = [this](bool stat, const std::string& value) {
            if (stat)
                records_ << "{\"type\":\"ssd_stat\"," << value.substr(1) << '\n';
            else if (log_.is_open())
                log_ << "{\"type\":\"ssd_interval\"," << value.substr(1) << '\n';
        };
        observation_->report(origin_, last_result_);
        if (controller_) {
            out_ << "{\"type\":\"buffer_summary\",\"capacity_bytes\":" << capacity_
                 << ",\"credit_wait_ns\":" << controller_->buffer().wait_ns()
                 << ",\"peak_bytes\":" << controller_->buffer().peak()
                 << ",\"admission_limit\":" << controller_->admission_limit()
                 << ",\"chunk_size\":" << controller_->chunk_size()
                 << ",\"block_bound\":" << controller_->block_bound() << "}\n";
            if (controller_->buffer().used() || controller_->buffer().waiting() ||
                controller_->port().pending() || controller_->compute().pending() ||
                controller_->sessions() || controller_->pending_protocol() || local_->pending() ||
                transport_->pending())
                throw std::logic_error("controller graph resources leaked");
        }
        for (auto& [q, v] : read_totals_)
            out_ << "{\"type\":\"ordinary_storage\",\"query\":" << q << ",\"requests\":" << v[0]
                 << ",\"useful_bytes\":" << v[1] << ",\"sector_bytes\":" << v[2] << "}\n";
        for (auto& [direction, v] : link_totals_)
            out_ << "{\"type\":\"link_summary\",\"destination\":" << direction
                 << ",\"transfers\":" << v[0] << ",\"payload_bytes\":" << v[1]
                 << ",\"wire_bytes\":" << v[2] << ",\"service_ns\":" << v[3] << "}\n";
        if (replay_mode_)
            records_ << ir::Json({{"type", "resident_summary"},
                                  {"reads", replay_reads_},
                                  {"logical_bytes", replay_bytes_},
                                  {"ssd_requests", 0},
                                  {"active_sessions", 0},
                                  {"peak_sessions", peak_active_}})
                            .dump()
                     << '\n';
        out_ << "{\"type\":\"path_summary\",\"ordinary_dma_bytes\":" << dma_
             << ",\"controller\":" << (controller_mode_ ? "true" : "false") << "}\n";
        records_ << "{\"type\":\"lifecycle\",\"runtime_tasks\":" << rt_->task_records()
                 << ",\"runtime_peak_tasks\":" << rt_->peak_tasks()
                 << ",\"waiters\":" << rt_->wait_records()
                 << ",\"leases\":" << handles_->lease_records()
                 << ",\"transitions\":" << rt_->transitions().size()
                 << ",\"releases\":" << handles_->releases().size()
                 << ",\"host_history\":" << cpu_->services().size()
                 << ",\"protocol_tasks\":" << (controller_ ? controller_->protocol_tasks() : 0)
                 << ",\"protocol_ranges\":" << (controller_ ? controller_->protocol_ranges() : 0)
                 << ",\"transport_history\":" << (transport_ ? transport_->records().size() : 0)
                 << ",\"resource_history\":"
                 << (controller_
                         ? controller_->storage().size() + controller_->buffer().records().size() +
                               controller_->port().records().size() +
                               controller_->compute().records().size()
                         : 0)
                 << "}\n";
        out_ << "{\"type\":\"summary\",\"queries\":" << completed_ << ",\"origin_ns\":" << origin_
             << ",\"end_ns\":" << last_result_ << ",\"drain_end_ns\":" << getCurrentSimTimeNano()
             << ",\"peak_inflight\":" << peak_active_ << ",\"admission_limit\":" << limit()
             << ",\"live_results\":0,\"live_content\":0,\"useful_bytes\":" << useful_
             << ",\"sector_bytes\":" << sectors_ * 512
             << ",\"services\":" << cpu_->observations().count << "}\n";

        if (semantic_.is_open())
            semantic_.flush();
        out_.flush();
        if (log_.is_open()) {
            log_.flush();
            if (!log_)
                throw std::runtime_error("graph log write failed");
        }
        if (!out_ || !records_)
            throw std::runtime_error("graph output write failed");
        sessions_.clear();
        observation_.reset();
        controller_.reset();
        transport_.reset();
        local_.reset();
        cpu_.reset();
        rt_.reset();
        bridge_.reset();
        device_.reset();
        std::puts("SST PASS graph");
    }

private:
    struct Read {
        uint64_t task = 0, query = 0;
        content::Handle content;
        std::unique_ptr<content::ReadAssociation> association;
    };
    runtime::Handle read(uint64_t q, content::Kind kind, uint32_t id) {
        auto state = std::make_shared<Read>();
        state->query = q;
        content::ObjectRef object{1, kind, id, 1};
        content::Selection selection{object, {0, store_->metadata(object).size}};
        runtime::Operation op;
        op.query = state->query;
        op.grant = [this, state, selection, id, kind](auto task) {
            state->task = task;
            state->association = std::make_unique<content::ReadAssociation>(
                *layout_,
                std::vector<content::Selection>{selection},
                content::Route::Ordinary,
                content::Location::HostMemory,
                content::Context{state->query, task, getCurrentSimTimeNano()});
            state->content = handles_->create({selection},
                                              content::Location::HostMemory,
                                              {state->query, task, getCurrentSimTimeNano()});
            if (semantic_.is_open())
                semantic_ << "{\"type\":\"read\",\"query\":" << state->query << ",\"kind\":\""
                          << (kind == content::Kind::Vector ? "vector" : "adjacency")
                          << "\",\"id\":" << id << "}\n";
            const auto& plan = state->association->plan();
            useful_ += plan.useful_bytes;
            sectors_ += plan.sector_bytes / 512;
            auto& counters = read_totals_[state->query];
            counters[0] += plan.extents.size();
            counters[1] += plan.useful_bytes;
            counters[2] += plan.sector_bytes;
            for (size_t i = 0; i < plan.extents.size(); ++i) {
                const auto& e = plan.extents[i];
                auto token = device_->window->read(e.lba, e.sectors);
                state->association->bind(i, token);
                requests_.emplace(token, state);
                out_ << "{\"type\":\"read_submit\",\"query\":" << state->query
                     << ",\"task\":" << task << ",\"request\":" << token << ",\"kind\":\""
                     << (kind == content::Kind::Vector ? "vector" : "adjacency")
                     << "\",\"id\":" << id << ",\"lba\":" << e.lba << ",\"sectors\":" << e.sectors
                     << ",\"time_ns\":" << getCurrentSimTimeNano() << "}\n";
            }
        };
        op.evaluate = [this, state](const auto&) {
            auto v = handles_->try_get(state->content, content::Consumer::HostExecutor, 1);
            handles_->release(state->content);
            return v.at(0);
        };
        op.service = [this](auto task) {
            rt_->service_done(task);
        };
        return rt_->submit(std::move(op));
    }
    uint64_t limit() const {
        return controller_ ? controller_->admission_limit() : max_inflight_;
    }
    void next() {
        while (replay_error_.empty() && submitted_ < nq_ && active_ < limit())
            start_query(++submitted_);
    }
    void retired(uint64_t q) {
        if (auto it = read_totals_.find(q); it != read_totals_.end()) {
            auto& v = it->second;
            records_ << "{\"type\":\"ordinary_storage\",\"query\":" << q << ",\"requests\":" << v[0]
                     << ",\"useful_bytes\":" << v[1] << ",\"sector_bytes\":" << v[2] << "}\n";
            read_totals_.erase(it);
        }
        decision_digest_.erase(q);
        --active_;
        ++completed_;
        bridge_->enqueue(
            getCurrentSimTimeNano(), EventBridge::ContinueSubmit, "admit query", [this, q] {
                replay_session_.reset();
                sessions_.erase(q);
                ir_sessions_.erase(q);
                records_ << "{\"type\":\"lifecycle_sample\",\"completed\":" << completed_
                         << ",\"runtime_tasks\":" << rt_->task_records()
                         << ",\"waiters\":" << rt_->wait_records()
                         << ",\"leases\":" << handles_->lease_records()
                         << ",\"sessions\":" << sessions_.size() + ir_sessions_.size() << "}\n";
                next();
            });
    }
    void start_query(uint64_t q) {
        ++active_;
        peak_active_ = std::max(peak_active_, active_);
        auto load_query = [this, q] {
            auto begin = queries_.begin() + (q - 1) * uint64_t(d_) * 4;
            return content::Bytes(begin, begin + uint64_t(d_) * 4);
        };
        auto qb = replay_mode_ ? content::Bytes{} : load_query();
        auto start = getCurrentSimTimeNano();
        decision_digest_[q] = 14695981039346656037ULL;
        search::GraphSearch::Score scorer;
        if (controller_ && !ir_mode_) {
            // Decode the validated FP32 query without giving the search state machine content access.
            std::vector<float> values;
            for (size_t i = 0; i < qb.size(); i += 4) {
                uint32_t bits = 0;
                for (unsigned j = 0; j < 4; ++j)
                    bits |= uint32_t(qb[i + j]) << (8 * j);
                float x;
                std::memcpy(&x, &bits, 4);
                values.push_back(x);
            }
            auto opened = controller_->open(q, 1, values);
            rt_->release(opened);
            scorer = [this, q](const std::vector<uint32_t>& nodes) {
                std::vector<content::Selection> selections;
                for (auto id : nodes)
                    selections.push_back(
                        {{1, content::Kind::Vector, id, 1}, {0, uint64_t(d_) * 4}});
                auto h = controller_->score(q, selections);
                auto observed = rt_->retain(h);
                rt_->when_all({observed}, q, [this, q, h = observed](auto outcome) {
                    if (outcome.failure)
                        throw std::runtime_error(outcome.failure->detail);
                    out_ << "{\"type\":\"score_visible\",\"query\":" << q
                         << ",\"time_ns\":" << getCurrentSimTimeNano() << ",\"scores\":";
                    score_json(out_, operators::scores(rt_->try_get(h)));
                    out_ << "}\n";
                    rt_->release(h);
                });
                return h;
            };
        }
        search::GraphSearch::Trace trace = [this, q](const search::Decision& d) {
            out_ << "{\"type\":\"decision\",\"query\":" << q << ",\"time_ns\":" << d.time
                 << ",\"candidates\":";
            score_json(out_, d.candidates);
            out_ << ",\"expand\":[";
            for (size_t i = 0; i < d.expand.size(); ++i) {
                if (i)
                    out_ << ',';
                out_ << d.expand[i];
            }
            out_ << "]}\n";
            std::ostringstream decision;
            score_json(decision, d.candidates);
            if (semantic_.is_open())
                semantic_ << "{\"type\":\"decision\",\"query\":" << q
                          << ",\"candidates\":" << decision.str()
                          << ",\"expand\":" << ir::Json(d.expand).dump() << "}\n";
            for (auto id : d.expand)
                decision << ':' << id;
            auto& digest = decision_digest_.at(q);
            for (unsigned char byte : decision.str()) {
                digest ^= byte;
                digest *= 1099511628211ULL;
            }
        };
        search::GraphSearch::Done done = [this, q, start](auto scores, auto expanded) {
            last_result_ = getCurrentSimTimeNano();
            std::ostringstream row;
            row << "{\"type\":\"query_result\",\"query\":" << q << ",\"offered_ns\":" << origin_
                << ",\"start_ns\":" << start << ",\"end_ns\":" << last_result_
                << ",\"admission_wait_ns\":" << start - origin_
                << ",\"offered_latency_ns\":" << last_result_ - origin_
                << ",\"latency_ns\":" << last_result_ - start << ",\"expanded\":" << expanded
                << ",\"decision_digest_fnv1a64\":" << decision_digest_.at(q)
                << ",\"insufficient\":" << (scores.size() < cfg_.k ? "true" : "false")
                << ",\"top_k\":";
            score_json(row, scores);
            row << "}\n";
            records_ << row.str();
            out_ << row.str();
            if (controller_ || ir_mode_) {
                auto closed = ir_mode_ ? mapping_->close(q) : controller_->close(q);
                rt_->when_all({closed}, q, [this, q, closed](auto o) {
                    if (o.failure)
                        throw std::runtime_error(o.failure->detail);
                    rt_->release(closed);
                    retired(q);
                });
            } else
                retired(q);
        };
        if (replay_mode_) {
            std::string line;
            if (!std::getline(replay_index_, line))
                throw std::runtime_error("missing preflight query index row");
            auto index = ir::Json::parse(line);
            auto original = index.at("query").get<uint32_t>();
            replay_session_ = std::make_unique<search::Replay>(
                *rt_,
                *cpu_,
                cfg_,
                q,
                original,
                d_,
                replay_ops_,
                std::move(index),
                load_query,
                [this, q](auto kind, auto node, auto task) {
                    content::ObjectRef object{1, kind, node, 1};
                    content::Selection selection{object, {0, store_->metadata(object).size}};
                    auto lease = handles_->create({selection},
                                                  content::Location::HostMemory,
                                                  {q, task, getCurrentSimTimeNano()});
                    try {
                        handles_->publish(lease);
                        auto data = handles_->try_get(lease, content::Consumer::HostExecutor, 1);
                        handles_->release(lease);
                        return std::move(data.at(0));
                    } catch (...) {
                        handles_->release(lease);
                        throw;
                    }
                },
                [this](const auto& event) {
                    if (event.at("type") == "replay_read") {
                        replay_bytes_ += event.at("bytes").template get<uint64_t>();
                        ++replay_reads_;
                    }
                    out_ << event.dump() << '\n';
                },
                std::move(trace),
                std::move(done),
                [this, q](auto error) {
                    replay_error_ = std::move(error);
                    retired(q);
                });
            replay_session_->start();
        } else if (ir_mode_) {
            std::vector<float> values(d_);
            for (size_t i = 0; i < values.size(); ++i) {
                uint32_t bits = 0;
                for (unsigned j = 0; j < 4; ++j)
                    bits |= uint32_t(qb[4 * i + j]) << (8 * j);
                std::memcpy(&values[i], &bits, 4);
            }
            auto initial = ir::StateSnapshot::initial(
                q,
                "search-state",
                {{"scored", ir::Json::array()}, {"expanded", ir::Json::array()}});
            mapping_->admit(q, std::move(values), initial);
            ir_sessions_[q] = std::make_unique<search::IrGraphSearch>(
                *executor_,
                [this](auto op, auto producers, auto states) {
                    return mapping_->submit(std::move(op), std::move(producers), std::move(states));
                },
                next_ir_op_,
                ir_contract_.manifest.run,
                q,
                q - 1,
                cfg_,
                d_,
                r_,
                controller_ ? ir::Placement::Controller : ir::Placement::Host,
                initial,
                [this] {
                    return getCurrentSimTimeNano();
                },
                std::move(trace),
                std::move(done));
            ir_sessions_.at(q)->start();
        } else {
            sessions_[q] = std::make_unique<search::GraphSearch>(
                *rt_,
                *cpu_,
                [this] {
                    return getCurrentSimTimeNano();
                },
                q,
                cfg_,
                std::move(qb),
                [this, q](auto kind, auto id) {
                    return read(q, kind, id);
                },
                std::move(trace),
                std::move(done),
                std::move(scorer));
            sessions_.at(q)->start();
        }
    }
    void wake(SST::Event* raw) {
        std::unique_ptr<GraphWake> e(static_cast<GraphWake*>(raw));
        --pending_;
        try {
            bridge_->wake(e->generation);
            if (local_)
                local_->poll();
            if (device_ && !measuring_ &&
                device_->window->advance(!Simulator->Peek_next_time(), getCurrentSimTimeNano())) {
                measuring_ = true;
                origin_ = device_->window->origin();
                records_ << "{\"type\":\"initial_state\",\"origin_ns\":" << origin_
                         << ",\"completed_preload_sectors\":"
                         << device_->window->completed_sectors()
                         << ",\"backend_pending\":" << device_->flow->pending()
                         << ",\"backend_drained\":"
                         << (!Simulator->Peek_next_time() ? "true" : "false")
                         << ",\"cmt_retained\":true,\"time_reset\":false}\n";
                observation_->reset_window();
                query_wall_start_ = std::chrono::steady_clock::now();
                next();
            }
            rt_->require_progress();
            bridge_->require_progress(replay_error_.empty() && completed_ != nq_,
                                      "graph/preload incomplete");
            if (!pending_ && bridge_->idle())
                primaryComponentOKToEndSim();
        } catch (const std::exception& ex) {
            getSimulationOutput().fatal(CALL_INFO, 1, "%s\n", ex.what());
        }
    }
    std::chrono::steady_clock::time_point wall_start_ = std::chrono::steady_clock::now(),
                                          query_wall_start_;
    bool ir_mode_ = false, replay_mode_ = false;
    std::ifstream replay_index_, replay_ops_;
    std::unique_ptr<search::Replay> replay_session_;
    std::string replay_error_;
    uint64_t replay_bytes_ = 0, replay_reads_ = 0;
    uint64_t next_ir_op_ = 0;
    ir::Document ir_contract_;
    std::unique_ptr<ir::Executor> executor_;
    std::unique_ptr<ir::Mapping> mapping_;
    std::map<uint64_t, std::unique_ptr<search::IrGraphSearch>> ir_sessions_;
    bool controller_mode_ = false;
    uint64_t capacity_ = 0, last_result_ = 0, active_ = 0, peak_active_ = 0;
    uint32_t submitted_ = 0, max_inflight_ = 1;
    std::map<uint64_t, uint64_t> decision_digest_;
    std::map<uint64_t, std::array<uint64_t, 3>> read_totals_;
    std::map<unsigned, std::array<uint64_t, 4>> link_totals_;
    std::unique_ptr<Observation> observation_;
    uint64_t dma_ = 0;
    std::unique_ptr<LocalReads> local_;
    std::unique_ptr<transport::PcieTransport> transport_;
    std::unique_ptr<model::Controller> controller_;
    search::Config cfg_;
    uint32_t d_ = 0, r_ = 0, nq_ = 0, completed_ = 0;
    uint64_t origin_ = 0, useful_ = 0, sectors_ = 0;
    bool measuring_ = false;
    unsigned pending_ = 0;
    SST::Link* link_;
    struct NullBuffer : std::streambuf {
        int overflow(int c) override {
            return traits_type::not_eof(c);
        }
    } null_;
    static ir::Json semantic_work(const operators::Work& w) {
        return {w.elements,
                w.flops,
                w.read_bytes,
                w.write_bytes,
                w.output_bytes,
                w.candidate_checks,
                w.comparisons};
    }
    void semantic_score(uint64_t query, uint64_t chunk, const operators::Result& result) {
        semantic_ << "{\"type\":\"score\",\"query\":" << query << ",\"chunk\":" << chunk
                  << ",\"work\":" << semantic_work(result.work) << ",\"scores\":";
        score_json(semantic_, operators::scores(result.bytes));
        semantic_ << "}\n";
    }
    std::ostream out_{&null_};
    OutputBudget output_budget_;
    BudgetFile records_{output_budget_}, log_{output_budget_}, semantic_{output_budget_};
    content::Bytes queries_;
    content::AnnLayout ann_;
    std::unique_ptr<content::ContentStore> store_;
    std::unique_ptr<content::Handles> handles_;
    std::unique_ptr<content::Layout> layout_;
    std::unique_ptr<OrdinaryDevice> device_;
    std::unique_ptr<EventBridge> bridge_;
    std::unique_ptr<runtime::Runtime> rt_;
    std::unique_ptr<model::HostCpu> cpu_;
    std::map<uint64_t, std::unique_ptr<search::GraphSearch>> sessions_;
    std::map<uint64_t, std::shared_ptr<Read>> requests_;
};
} // namespace ndpsim
