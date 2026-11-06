#include "sst/dag_host.h"
#include <fstream>
#include <nlohmann/json.hpp>
namespace ndpsim::dag {
namespace {
using Json = nlohmann::json;
Json config(SST::Params &p) {
    Json c;
    std::ifstream(p.find<std::string>("config")) >> c;
    return c;
}
HostInputs inputs(SST::Params &p) {
    auto c = config(p);
    HostInputs in;
    in.package = c.at("package").get<std::string>();
    in.scratch = c.at("scratch").get<std::string>();
    in.receipt.report = c.at("report").get<std::string>();
    in.receipt.report_sha256 = c.at("report_sha256");
    in.receipt.package_sha256 = c.at("package_sha256");
    in.receipt.registry_sha256 = c.at("registry_sha256");
    in.receipt.allow_fixture = true;
    in.config.admission = c.value("admission", 1);
    in.config.workers = c.value("workers", 1);
    if (c.contains("offered_ns"))
        in.config.offered_ns = c.at("offered_ns").get<std::vector<uint64_t>>();
    return in;
}
} // namespace
class DagProbe : public GraphHost {
  public:
    SST_ELI_REGISTER_COMPONENT(DagProbe, "ndpsim_dag_validation", "GraphHost",
                               SST_ELI_ELEMENT_VERSION(0, 4, 5),
                               "Generic DAG SST integration fixture",
                               COMPONENT_CATEGORY_UNCATEGORIZED)
    SST_ELI_DOCUMENT_PARAMS({"config", "P08 fixture metadata and services", ""})
    DagProbe(SST::ComponentId_t id, SST::Params &p) : GraphHost(id, inputs(p)), c_(config(p)) {
        cpu_ = std::make_unique<timing::HostCpu>(
            bridge(), [this] { return getCurrentSimTimeNano(); }, c_.value("slots", 2));
        cpu_->observations().enabled = false;
    }

  protected:
    timing::Operation lower(const Stage &s) override {
        timing::Operation o;
        o.key = s.key;
        o.work = s.metadata.work;
        o.features = s.metadata.features;
        o.outputs = s.metadata.outputs;
        o.resource = cpu_.get();
        o.duration_ns = c_.at("durations").at(s.key.op);
        uint64_t delay = c_.value("returns", Json::object()).value(s.key.op, uint64_t(0));
        if (delay)
            o.send_return = [this, delay](timing::Done done) {
                bridge().enqueue(timing::checked_add(getCurrentSimTimeNano(), delay),
                                 EventBridge::Backend, "SST actual return", [done] { done({}); });
            };
        return o;
    }
    void on_request(const RequestRecord &r) override {
        requests_.push_back({{"query", r.query},
                             {"worker_start", r.worker_start},
                             {"visible", r.visible},
                             {"drained", r.drained}});
    }
    void on_stage(const StageRecord &s) override {
        stages_.push_back({{"query", s.key.query},
                           {"op", s.key.op},
                           {"submitted", s.submitted},
                           {"completed", s.completed}});
    }
    void on_drained(timing::Outcome o) override {
        if (o.failure)
            throw std::runtime_error(o.failure->detail);
        auto &m = executor().metrics();
        Json result = {{"success", true},
                       {"drained", drained_time()},
                       {"requests", requests_},
                       {"stages", stages_},
                       {"active_stages", m.active_stages},
                       {"outstanding", m.outstanding},
                       {"runtime_tasks", runtime().task_records()},
                       {"resource_pending", cpu_->pending()},
                       {"peak_workers", m.peak_workers}};
        std::ofstream(c_.at("output").get<std::string>()) << result.dump(2) << '\n';
        std::puts("P08 SST PASS dependency DAG and query drain");
    }

  private:
    Json c_, requests_ = Json::array(), stages_ = Json::array();
    std::unique_ptr<timing::HostCpu> cpu_;
};
} // namespace ndpsim::dag
