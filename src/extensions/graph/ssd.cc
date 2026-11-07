#include "extensions/graph/ssd.h"
#include <set>
namespace ndpsim::graph {
namespace {
using J = nlohmann::json;
void need(bool b, const std::string& why) {
    if (!b)
        throw std::runtime_error("graph SSD: " + why);
}
uint64_t mul(uint64_t a, uint64_t b) {
    need(!b || a <= UINT64_MAX / b, "overflow");
    return a * b;
}
uint64_t feature(const dag::Stage& s, const std::string& n) {
    return s.metadata.features.at(n).first;
}
J counters(const capture::Counters& c) {
    J j = J::object();
    for (const auto& p : c)
        j[p.first] = {{"value", p.second.first}, {"unit", p.second.second}};
    return j;
}
J read_json(const std::filesystem::path& p) {
    std::ifstream f(p);
    need(bool(f), "cannot open " + p.string());
    J j;
    f >> j;
    return j;
}
} // namespace
std::string SsdRun::prep_key(const dag::Stage& s) {
    need(bool(s.metadata.chunk), "missing semantic chunk");
    return std::to_string(s.key.query) + ":" + s.key.op + ":" + std::to_string(*s.metadata.chunk);
}
SsdRun::SsdRun(const std::filesystem::path& path, EventBridge& b, EventBridge::Clock now)
    : bridge_(b), now_(now), costs_(path, true), config_(costs_.snapshot.at("config")),
      runtime_(b, now), cpu_(b, now, costs_.slots), endpoint_(b, now, 1) {
    offload_ = config_.at("placement") == "controller";
    need(offload_ || config_.at("placement") == "host", "unknown placement/mapping");
    context_ = offload_ ? "ssd_controller_command/1" : "ssd_host_return/1";
    need(config_.at("context") == context_,
         "placement context mismatch; resident Profile forbidden");
    need(costs_.inputs.config.workers == 1 && costs_.inputs.config.admission == 1 &&
             costs_.slots == 1,
         "SSD currently requires FIFO workers/slots/admission=1/1/1");
    auto manifest = read_json(costs_.inputs.package / "manifest.json");
    std::vector<capture::Object> objects;
    for (const auto& o : manifest.at("objects")) {
        capture::Object x;
        x.id = o.at("id");
        x.version = o.at("version");
        x.type = o.at("type");
        x.size_bytes = o.at("size_bytes");
        x.shape = o.at("shape").get<std::vector<uint64_t>>();
        objects.push_back(x);
        if (x.id == "vectors") {
            need(x.shape.size() == 2, "vector shape");
            dimension_ = x.shape[1];
        }
    }
    need(dimension_ && dimension_ <= UINT32_MAX, "dimension");
    auto absolute = std::filesystem::absolute(path);
    layout_ = std::make_unique<layout::Layout>(
        config_.at("layout").get<std::string>(), absolute, objects);
    device_config_ =
        std::make_unique<DeviceConfig>(config_.at("device").get<std::string>(), absolute);
    device_config_->validate_layout(
        *layout_,
        ((layout_->mapped_end_sectors() + device_config_->geometry().page_sectors - 1) /
         device_config_->geometry().page_sectors * device_config_->geometry().page_sectors));
    auto r = config_.at("resources");
    const std::set<std::string> fields = {
        "capacity_bytes", "query_bound", "block_bound", "staging_bytes_per_ns", "compute_slots"};
    need(r.is_object() && r.size() == fields.size(), "resource fields");
    for (const auto& k : fields)
        need(r.contains(k) && r[k].is_number_unsigned() && r[k].get<uint64_t>() > 0,
             "resource integer");
    resources_ = {r["capacity_bytes"],
                  r["query_bound"],
                  r["block_bound"],
                  1,
                  r["staging_bytes_per_ns"],
                  r["compute_slots"]};
    need(resources_.query_bound >= mul(4, dimension_) &&
             resources_.block_bound <= resources_.capacity_bytes &&
             resources_.query_bound <= resources_.capacity_bytes - resources_.block_bound,
         "query/block capacity");
    // Fixed conservative item sector upper bound, independent of instantaneous credit.
    // A vector can intersect multiple layout extents: preflight computes the actual maximum.
    package_ = std::make_unique<dag::Package>(
        costs_.inputs.package, costs_.inputs.receipt, costs_.inputs.scratch, costs_.inputs.limits);
    uint64_t max_item = 0;
    for (size_t q = 0; q < package_->queries().size(); ++q) {
        auto g = package_->graph(q);
        for (const auto& node : g.nodes) {
            auto s = package_->stage(node);
            if (s.metadata.kind == "score_prepare")
                for (const auto& a : s.metadata.accesses)
                    max_item = std::max(max_item, layout_->resolve({a}).sector_bytes);
        }
    }
    need(max_item > 0, "no graph vector accesses");
    const auto per =
        timing::checked_add(max_item, 16); // IDs 4 + result 12; command header 64 per block.
    need(resources_.block_bound > 64, "block command capacity");
    items_per_block_ = (resources_.block_bound - 64) / per;
    need(items_per_block_ > 0, "one complete vector cannot fit");
    // Validate every mapping/model before creating or injecting into the device.
    for (size_t q = 0; q < package_->queries().size(); ++q) {
        auto g = package_->graph(q);
        for (const auto& node : g.nodes) {
            auto s = package_->stage(node);
            auto e = effective(s);
            if (s.metadata.kind == "score_prepare")
                prepared_[prep_key(s)] = s.metadata.accesses;
            if (s.metadata.kind == "score_l2" && offload_) {
                auto it = prepared_.find(prep_key(s));
                need(it != prepared_.end(), "L2 missing prepare");
                auto bs = blocks(s, it->second);
                for (auto& block : bs)
                    costs_.lower(effective(block.stage), cpu_, "controller");
                prepared_.erase(it);
            } else
                costs_.lower(e, cpu_);
            if (s.metadata.kind == "init" || s.metadata.kind == "adjacency_prepare" ||
                (s.metadata.kind == "score_prepare" && !offload_)) {
                auto plan = layout_->resolve(s.metadata.accesses);
                for (const auto& read : plan.reads)
                    need(read.sectors && read.sectors <= 65535, "Unsupported ordinary extent size");
            }
        }
        prepared_.clear();
    }
    trace_.open(costs_.output);
    need(bool(trace_), "cannot create trace");
    emit({{"type", "configuration"},
          {"snapshot", costs_.snapshot},
          {"layout", J::parse(layout_->effective_json())},
          {"layout_sha256", layout_->source_sha256()},
          {"device", J::parse(device_config_->effective_json())},
          {"device_sha256", device_config_->source_sha256()},
          {"items_per_block", items_per_block_}});
    device_ = std::make_unique<OrdinaryDevice>(
        ((layout_->mapped_end_sectors() + device_config_->geometry().page_sectors - 1) /
         device_config_->geometry().page_sectors * device_config_->geometry().page_sectors),
        *device_config_,
        *layout_);
    local_ = std::make_unique<timing::MetadataLocalReads>(
        *static_cast<SSD_Components::Host_Interface_NVMe*>(device_->device->Host_interface),
        b,
        now);
    pcie_ = std::make_unique<timing::MetadataPcie>(*device_->link, b, now, rules_);
    controller_ = std::make_unique<timing::MetadataController>(
        runtime_, *local_, *pcie_, rules_, b, now, resources_);
    runtime_.observations().enabled = false;
    cpu_.observations().sink = [this](const auto& x) {
        emit({{"type", "cpu"},
              {"key", x.key},
              {"submitted", x.submitted},
              {"start", x.start},
              {"end", x.end},
              {"duration_ns", x.duration_ns}});
    };
    controller_->observations().sink = [this](const auto& x) {
        emit({{"type", "buffer_access"},
              {"key", x.key},
              {"bytes", x.bytes},
              {"submitted", x.submitted},
              {"duration_ns", x.duration_ns}});
    };
    controller_->compute_observations().sink = [this](const auto& x) {
        emit({{"type", "controller_compute"},
              {"key", x.key},
              {"submitted", x.submitted},
              {"start", x.start},
              {"end", x.end},
              {"duration_ns", x.duration_ns}});
    };
    controller_->port_observations().enabled = false;
    controller_->credit_observations().sink = [this](const auto& x) {
        emit({{"type", "credit"},
              {"time", x.time},
              {"bytes", x.bytes},
              {"used", x.used},
              {"reserve", x.reserve}});
    };
    local_->observe = [this](const auto& c) {
        emit({{"type", "local_plan"},
              {"query", c.context.query},
              {"op", c.context.op},
              {"stage", c.context.stage},
              {"logical_bytes", c.plan.logical_bytes},
              {"sector_bytes", c.plan.sector_bytes}});
        for (const auto& r : c.requests) {
            for (const auto& t : r.transactions)
                emit({{"type", "nand_transaction"},
                      {"id", t.id},
                      {"bytes", t.bytes},
                      {"time", t.time}});
            ++local_requests_;
            emit({{"type", "local_read"},
                  {"query", c.context.query},
                  {"op", c.context.op},
                  {"stage", c.context.stage},
                  {"lba", r.lba},
                  {"sectors", r.sectors},
                  {"completed", r.time}});
        }
    };
    pcie_->observations().sink = [this](const auto& x) {
        emit({{"type", "protocol"},
              {"kind", int(x.kind)},
              {"direction", int(x.direction)},
              {"query", x.query},
              {"task", x.task},
              {"chunk", x.chunk},
              {"count", x.count},
              {"dimension", x.dimension},
              {"logical", x.logical},
              {"payload", x.payload},
              {"wire", x.wire},
              {"submitted", x.submitted},
              {"arrived", x.arrived}});
    };
    device_->link->observe = [this](const char* event, const auto& x) {
        std::string layer = "platform";
        using namespace Host_Components;
        if (x.type == PCIe_Message_Type::READ_REQ)
            layer = "sq_fetch";
        else if (x.type == PCIe_Message_Type::READ_COMP)
            layer = "sq_entry";
        else if (x.type == PCIe_Message_Type::WRITE_REQ)
            layer = x.destination == PCIe_Destination_Type::DEVICE
                        ? "doorbell"
                        : (x.payload == 16 ? "cq_entry" : "data");
        if (launched_)
            emit({{"type", "pcie"},
                  {"layer", layer},
                  {"event", event},
                  {"id", x.id},
                  {"platform_id", x.platform_id},
                  {"time", x.time},
                  {"message_type", int(x.type)},
                  {"destination", int(x.destination)},
                  {"payload", x.payload},
                  {"wire", x.wire},
                  {"service", x.service}});
    };
    executor_ = std::make_unique<dag::Executor>(
        *package_, runtime_, b, now, costs_.inputs.config, [this](const auto& s) {
            return lower(s);
        });
    executor_->stages().sink = [this](const auto& x) {
        emit({{"type", "stage"},
              {"query", x.key.query},
              {"op", x.key.op},
              {"stage", x.key.stage},
              {"submitted", x.submitted},
              {"completed", x.completed},
              {"success", x.success}});
    };
    executor_->requests().sink = [this](const auto& x) {
        emit({{"type", "request"},
              {"query", x.query},
              {"admitted", x.admitted},
              {"visible", x.visible},
              {"drained", x.drained},
              {"success", x.success}});
    };
}
SsdRun::~SsdRun() = default;
void SsdRun::emit(Json j) {
    trace_ << j.dump() << '\n';
    trace_.flush();
    need(bool(trace_), "trace write");
}
dag::Stage SsdRun::effective(dag::Stage s) const {
    need(s.operator_version == "2", "unknown graph semantic version");
    static const std::map<std::string, std::set<std::string>> supported = {
        {"graph.INIT", {"init"}},
        {"graph.MARK_SCORED", {"state"}},
        {"graph.MARK_EXPANDED", {"state"}},
        {"graph.SCORE_L2", {"score_prepare", "score_l2", "merge_append"}},
        {"graph.MERGE_CANDIDATES", {"merge_finalize"}},
        {"graph.SELECT_FRONTIER", {"select"}},
        {"graph.READ_ADJACENCY", {"adjacency_prepare"}},
        {"graph.JOIN", {"join"}},
        {"graph.DEDUP_NEIGHBORS", {"dedup"}},
        {"graph.FINISH", {"finish"}}};
    auto i = supported.find(s.operator_id);
    need(i != supported.end() && i->second.count(s.metadata.kind), "unknown graph mapping");
    need(s.metadata.context == "resident_random/2" || s.metadata.context == context_,
         "unsupported capture context");
    s.metadata.context = context_;
    auto& f = s.metadata.features;
    if (s.metadata.kind == "score_prepare" || s.metadata.kind == "adjacency_prepare") {
        f.at("state_objects").first = 0;
        if (s.metadata.kind == "score_prepare") {
            auto bytes = mul(mul(feature(s, "vectors"), dimension_), 4);
            f.at("copied_bytes").first = offload_ ? 0 : bytes;
            f.at("decoded_bytes").first = offload_ ? 0 : bytes;
        }
    }
    if (s.metadata.kind == "merge_append" && offload_)
        f.at("released_vectors").first = 0;
    return s;
}
std::vector<SsdRun::Block> SsdRun::blocks(const dag::Stage& s,
                                          const std::vector<capture::Access>& accesses) const {
    need(s.metadata.decomposition_rule == "graph-l2-items/1", "Unsupported Work decomposition");
    const auto n = feature(s, "vectors"), d = feature(s, "dimensions");
    need(d == dimension_ && n == accesses.size() && n > 0, "L2 item cardinality");
    capture::Counters expected = {
        {"elements", {mul(n, d), "count"}},
        {"flops", {mul(mul(3, n), d), "flop"}},
        {"read_bytes", {timing::checked_add(mul(mul(4, n), d), mul(4, d)), "B"}},
        {"write_bytes", {mul(12, n), "B"}},
        {"output_bytes", {mul(12, n), "B"}},
        {"candidate_checks", {0, "count"}},
        {"comparisons", {0, "count"}}};
    need(s.metadata.work == expected, "L2 Work rule mismatch");
    for (const auto& a : accesses)
        need(a.object == "vectors" && a.length_bytes == mul(4, d),
             "indivisible complete vector required");
    std::vector<Block> result;
    for (size_t begin = 0; begin < n;) {
        uint64_t count = std::min(n - begin, items_per_block_);
        std::vector<capture::Access> a(accesses.begin() + begin, accesses.begin() + begin + count);
        auto child = s;
        child.metadata.features["vectors"].first = count;
        child.metadata.work = expected;
        auto& w = child.metadata.work;
        w["elements"].first = mul(count, d);
        w["flops"].first = mul(mul(3, count), d);
        w["read_bytes"].first = timing::checked_add(mul(mul(4, count), d), mul(4, d));
        w["write_bytes"].first = w["output_bytes"].first = mul(12, count);
        child.metadata.outputs = {{"scores", "graph.scores", mul(12, count), 0, ""}};
        auto plan = layout_->resolve(a);
        need(timing::checked_add(plan.sector_bytes, timing::checked_add(64, mul(16, count))) <=
                 resources_.block_bound,
             "physical block bound");
        if (count != n) {
            for (const auto& r : costs_.snapshot.at("profile").at("rules"))
                if (r.at("operator") == s.operator_id && r.at("stage_kind") == s.metadata.kind &&
                    r.at("location") == "controller" && r.at("context") == context_)
                    need(r.at("source") != "measured-table",
                         "Unsupported splitting per-call measured table");
        }
        result.push_back({std::move(plan), std::move(child), count});
        begin += count;
    }
    return result;
}
void SsdRun::await(timing::CompletionToken token, timing::Done done) {
    runtime_.when_complete(token, [this, token, done](auto x) {
        runtime_.release(token);
        done(x);
    });
}
timing::Operation SsdRun::lower(const dag::Stage& original) {
    auto s = effective(original);
    const auto kind = s.metadata.kind;
    emit({{"type", "logical"},
          {"query", s.key.query},
          {"op", s.key.op},
          {"stage", s.key.stage},
          {"work", counters(original.metadata.work)},
          {"features", counters(original.metadata.features)}});
    timing::Operation op;
    if (kind == "score_l2" && offload_) {
        auto i = prepared_.find(prep_key(s));
        need(i != prepared_.end(), "L2 missing vector plan");
        auto bs = blocks(s, i->second);
        prepared_.erase(i);
        // Evaluate every physical call against its actual Work and applicability; never divide ns.
        std::vector<timing::ControllerRequest> requests;
        for (auto& block : bs) {
            auto service = costs_.lower(effective(block.stage), cpu_, "controller");
            service.resource = nullptr;
            timing::ControllerRequest r;
            r.command.kind = timing::MessageKind::Execute;
            r.command.query = s.key.query;
            r.command.version = 1;
            r.command.chunk = chunk_sequence_ = timing::checked_add(chunk_sequence_, 1);
            service.service_key =
                J({{"query", s.key.query},
                   {"op", s.key.op},
                   {"stage", s.key.stage},
                   {"physical_chunk", r.command.chunk},
                   {"profile_sha256", costs_.snapshot.at("config").at("profile_sha256")}})
                    .dump();
            J extents = J::array();
            for (const auto& read : block.reads.reads)
                extents.push_back({{"lba", read.lba}, {"sectors", read.sectors}});
            emit({{"type", "demand"},
                  {"physical", true},
                  {"physical_chunk", r.command.chunk},
                  {"detail", costs_.last_demand},
                  {"reads", extents},
                  {"semantic_chunk", s.metadata.chunk.value()}});
            r.command.count = block.count;
            r.command.dimension = dimension_;
            r.result = r.command;
            r.result.kind = timing::MessageKind::Result;
            r.result.dimension = 0;
            r.scratch_bytes = timing::checked_add(64, mul(4, block.count));
            r.scratch_access_bytes = mul(4, block.count);
            r.reads = std::move(block.reads);
            r.operation = std::move(service);
            requests.push_back(std::move(r));
        }
        op.key = s.key;
        op.outputs = s.metadata.outputs;
        op.resource = &endpoint_;
        op.input = [this, requests = std::move(requests)](timing::Done done) mutable {
            // Submit fixed blocks together; resource credit owns admission and actual FIFO service.
            auto remaining = std::make_shared<size_t>(requests.size());
            auto failure = std::make_shared<timing::Outcome>();
            for (auto& r : requests) {
                auto t = controller_->execute(std::move(r));
                await(t, [remaining, failure, done](auto x) {
                    if (x.failure)
                        *failure = x;
                    if (--*remaining == 0)
                        done(*failure);
                });
            }
        };
    } else {
        op = costs_.lower(s, cpu_);
        emit({{"type", "demand"}, {"physical", false}, {"detail", costs_.last_demand}});
        if (kind == "init" || kind == "adjacency_prepare" ||
            (kind == "score_prepare" && !offload_)) {
            auto plan = layout_->resolve(s.metadata.accesses);
            op.input = [this, plan, kind](auto done) {
                ordinary(plan, kind, std::move(done));
            };
        }
        if (kind == "score_prepare" && offload_)
            prepared_[prep_key(s)] = s.metadata.accesses;
        if (kind == "init" && offload_)
            op.send_return = [this, q = s.key.query](auto done) {
                timing::Message m;
                m.query = q;
                m.kind = timing::MessageKind::Open;
                m.version = 1;
                m.count = m.dimension = dimension_;
                await(controller_->open(m), std::move(done));
            };
        if (kind == "finish" && offload_)
            op.send_return = [this, q = s.key.query](auto done) {
                await(controller_->close(q), std::move(done));
            };
    }
    // The DAG token retains captured semantics; demand lowering is separately traced.
    op.work = original.metadata.work;
    op.features = original.metadata.features;
    return op;
}
void SsdRun::ordinary(layout::Plan plan, std::string kind, timing::Done done) {

    for (const auto& r : plan.reads)
        need(r.sectors && r.sectors <= 65535 && r.lba < device_->namespace_sectors &&
                 r.sectors <= device_->namespace_sectors - r.lba,
             "ordinary extent");
    emit({{"type", "ordinary_plan"},
          {"kind", kind},
          {"logical_bytes", plan.logical_bytes},
          {"sector_bytes", plan.sector_bytes}});
    ordinary_queue_.push_back({std::move(plan), std::move(kind), std::move(done)});
    if (!ordinary_busy_) {
        ordinary_busy_ = true;
        ordinary_next();
    }
}
void SsdRun::ordinary_next() {
    auto& p = ordinary_queue_.front();
    if (p.plan.reads.empty()) {
        auto done = std::move(p.done);
        ordinary_queue_.pop_front();
        ordinary_busy_ = false;
        if (!ordinary_queue_.empty()) {
            ordinary_busy_ = true;
            ordinary_next();
        }
        bridge_.enqueue(now_(), EventBridge::ResourceCompleteRelease, "ordinary parent", [done] {
            done({});
        });
        return;
    }
    current_ordinary_kind_ = p.kind;
    auto r = p.plan.reads.front();
    p.plan.reads.erase(p.plan.reads.begin());
    ++ordinary_requests_;
    emit({{"type", "ordinary_submit"},
          {"kind", current_ordinary_kind_},
          {"lba", r.lba},
          {"sectors", r.sectors},
          {"sq_tail_bytes", 2},
          {"sq_entry_bytes", sizeof(Submission_Queue_Entry)}});
    if (current_ordinary_kind_ == "score_prepare")
        vector_d2h_ = timing::checked_add(vector_d2h_, mul(r.sectors, 512));
    Simulator->Synchronize_now(now_());
    auto expected = std::make_shared<uint64_t>();
    ordinary_done_ = [this, r, expected](auto c) {
        need(c.token == *expected && c.lba == r.lba && c.sectors == r.sectors && !c.write &&
                 c.time == now_(),
             "ordinary completion");
        emit({{"type", "ordinary_read"},
              {"kind", current_ordinary_kind_},
              {"lba", c.lba},
              {"sectors", c.sectors},
              {"completed", c.time},
              {"cq_entry_bytes", 16},
              {"cq_head_bytes", 2}});
        bridge_.enqueue(
            now_(), EventBridge::ResourceCompleteRelease, "ordinary data and CQ", [this] {
                ordinary_next();
            });
    };
    *expected = device_->flow->submit(r.lba, r.sectors);
}
void SsdRun::start() {
    device_->start();
    bridge_.start();
}
void SsdRun::poll() {
    local_->poll();
    if (!launched_ && device_->window->advance(!Simulator->Peek_next_time(), now_())) {
        launched_ = true;
        device_->flow->completed = [this](auto c) {
            need(bool(ordinary_done_), "orphan ordinary completion");
            auto done = std::move(ordinary_done_);
            done(c);
        };
        emit({{"type", "measurement_start"}, {"time", now_()}});
        executor_->start([this](auto x) {
            finished_ = true;
            success_ = !x.failure;
        });
    }
    if (finished_ && bridge_.idle()) {
        verify();
        emit({{"type", "summary"},
              {"success", success_},
              {"full_acceptance_passed", false},
              {"ordinary_requests", ordinary_requests_},
              {"local_requests", local_requests_},
              {"vector_d2h", vector_d2h_},
              {"runtime_tasks", runtime_.task_records()},
              {"pins", runtime_.pins()},
              {"buffer_used", controller_->buffer().used()},
              {"sessions", controller_->sessions()},
              {"configured_unvalidated", true},
              {"drained", now_()}});
    }
}
void SsdRun::verify() const {
    need(finished_ && executor_->drained() && !runtime_.task_records() && !runtime_.pins() &&
             !runtime_.owners(),
         "DAG/runtime not drained");
    need(!ordinary_busy_ && !device_->flow->pending() && !local_->pending() && !pcie_->pending() &&
             !controller_->sessions() && !controller_->pending_jobs() &&
             !controller_->pending_protocol() && !controller_->buffer().used() &&
             !controller_->buffer().waiting() && !cpu_.pending() &&
             !controller_->compute().pending() && !controller_->port().pending(),
         "SSD resources not drained");
    need(!offload_ || !vector_d2h_, "Controller vector returned to Host");
}
} // namespace ndpsim::graph
