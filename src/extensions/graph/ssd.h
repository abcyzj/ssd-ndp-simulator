#pragma once
#include "mapping/memory.h"
#include "backend/ordinary_device.h"
#include "timing/controller.h"
namespace ndpsim::graph {
// Graph-specific lowering. The generic DAG and resources know no graph syntax.
class SsdRun {
public:
    SsdRun(const std::filesystem::path&, EventBridge&, EventBridge::Clock);
    ~SsdRun();
    void start();
    void poll();
    bool finished() const {
        return finished_;
    }
    bool success() const {
        return success_;
    }
    void verify() const;

private:
    using Json = nlohmann::json;
    struct Block {
        layout::Plan reads;
        dag::Stage stage;
        uint64_t count;
    };
    EventBridge& bridge_;
    EventBridge::Clock now_;
    memory::Plan costs_;
    Json config_;
    std::unique_ptr<layout::Layout> layout_;
    std::unique_ptr<DeviceConfig> device_config_;
    std::unique_ptr<dag::Package> package_;
    timing::Runtime runtime_;
    timing::HostCpu cpu_;
    timing::FifoResource endpoint_;
    std::unique_ptr<OrdinaryDevice> device_;
    timing::MessageRules rules_;
    std::unique_ptr<timing::MetadataLocalReads> local_;
    std::unique_ptr<timing::MetadataPcie> pcie_;
    std::unique_ptr<timing::MetadataController> controller_;
    std::unique_ptr<dag::Executor> executor_;
    std::ofstream trace_;
    timing::ControllerResources resources_;
    std::map<std::string, std::vector<capture::Access>> prepared_;
    std::function<void(ExternalFlow::Completion)> ordinary_done_;
    struct OrdinaryJob {
        layout::Plan plan;
        std::string kind;
        timing::Done done;
    };
    std::deque<OrdinaryJob> ordinary_queue_;
    bool ordinary_busy_ = false, launched_ = false, finished_ = false, success_ = false;
    uint64_t dimension_ = 0, items_per_block_ = 0, ordinary_requests_ = 0, local_requests_ = 0;
    uint64_t vector_d2h_ = 0, chunk_sequence_ = 0;
    bool offload_;
    std::string context_, current_ordinary_kind_;
    void emit(Json);
    timing::Operation lower(const dag::Stage&);
    dag::Stage effective(dag::Stage) const;
    std::vector<Block> blocks(const dag::Stage&, const std::vector<capture::Access>&) const;
    void ordinary(layout::Plan, std::string kind, timing::Done);
    void ordinary_next();
    void await(timing::CompletionToken, timing::Done);
    static std::string prep_key(const dag::Stage&);
};
} // namespace ndpsim::graph
