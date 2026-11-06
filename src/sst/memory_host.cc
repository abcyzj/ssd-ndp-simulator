#include "mapping/memory.h"
#include "sst/dag_host.h"
#include <fstream>
namespace ndpsim::memory {
using Json = nlohmann::json;
class MemoryHost : public dag::GraphHost {
public:
    SST_ELI_REGISTER_COMPONENT(MemoryHost,
                               "ndpsim_memory",
                               "Host",
                               SST_ELI_ELEMENT_VERSION(0, 4, 5),
                               "Timing-only resident Host",
                               COMPONENT_CATEGORY_UNCATEGORIZED)
    SST_ELI_DOCUMENT_PARAMS({"config", "ndpsim-memory-run/1 config path", ""})
    MemoryHost(SST::ComponentId_t id, SST::Params& p)
        : MemoryHost(id, std::make_unique<Plan>(p.find<std::string>("config"))) {
    }
    void finish() override {
        GraphHost::finish();
        if (!success_)
            throw std::runtime_error("memory execution failed (see trace)");
    }

private:
    std::unique_ptr<Plan> plan_;
    std::unique_ptr<timing::HostCpu> cpu_;
    std::ofstream trace_;
    uint64_t demand_ = 0;
    bool success_ = false;
    void emit(Json row) {
        trace_ << row.dump() << '\n';
        trace_.flush();
        if (!trace_)
            throw std::runtime_error("memory trace write failed");
    }
    MemoryHost(SST::ComponentId_t id, std::unique_ptr<Plan> plan)
        : GraphHost(id, plan->inputs), plan_(std::move(plan)) {
        trace_.open(plan_->output);
        if (!trace_)
            throw std::runtime_error("cannot create memory trace");
        emit({{"type", "configuration"}, {"snapshot", plan_->snapshot}});
        cpu_ = std::make_unique<timing::HostCpu>(
            bridge(),
            [this] {
                return getCurrentSimTimeNano();
            },
            plan_->slots);
        cpu_->observations().enabled = false;
        cpu_->observations().sink = [this](const timing::ServiceRecord& r) {
            emit({{"type", "service"},
                  {"id", r.id},
                  {"key", r.key},
                  {"submitted", r.submitted},
                  {"start", r.start},
                  {"end", r.end},
                  {"duration_ns", r.duration_ns}});
        };
    }
    timing::Operation lower(const dag::Stage& s) override {
        auto op = plan_->lower(s, *cpu_);
        demand_ = timing::checked_add(demand_, op.duration_ns);
        auto row = plan_->last_demand;
        row["type"] = "demand";
        row["time"] = getCurrentSimTimeNano();
        emit(row);
        return op;
    }
    void on_stage(const dag::StageRecord& s) override {
        emit({{"type", "stage"},
              {"query", s.key.query},
              {"op", s.key.op},
              {"stage", s.key.stage},
              {"submitted", s.submitted},
              {"completed", s.completed},
              {"success", s.success}});
    }
    void on_request(const dag::RequestRecord& r) override {
        emit({{"type", "request"},
              {"query", r.query},
              {"offered", r.offered},
              {"admitted", r.admitted},
              {"worker_start", r.worker_start},
              {"visible", r.visible},
              {"drained", r.drained},
              {"success", r.success}});
    }
    void on_drained(timing::Outcome o) override {
        success_ = !o.failure;
        const auto& m = executor().metrics();
        emit({{"type", "summary"},
              {"success", success_},
              {"error", o.failure ? o.failure->detail : ""},
              {"drained", drained_time()},
              {"cpu_demand_ns", demand_},
              {"peak_workers", m.peak_workers},
              {"peak_slots", cpu_->peak_busy()},
              {"active_stages", m.active_stages},
              {"outstanding", m.outstanding},
              {"runtime_tasks", runtime().task_records()},
              {"pins", runtime().pins()},
              {"resource_pending", cpu_->pending()},
              {"ssd_requests", 0},
              {"device_instances", 0},
              {"full_acceptance_passed", false}});
    }
};
} // namespace ndpsim::memory
