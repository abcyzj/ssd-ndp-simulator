#include <sst/core/sst_config.h>
#include <sst/core/component.h>
#include <sst/core/link.h>
#include "../../tests/ir_scenario.h"

namespace ndpsim {
class IrWake : public SST::Event {
public:
    explicit IrWake(uint64_t g) : generation(g) {
    }
    uint64_t generation;
};
class IrValidation : public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(IrValidation,
                               "ndpsim",
                               "IrValidation",
                               SST_ELI_ELEMENT_VERSION(0, 3, 0),
                               "Dynamic IR boundary probe",
                               COMPONENT_CATEGORY_UNCATEGORIZED)
    SST_ELI_DOCUMENT_PARAMS({"contract", "synthetic IR contract", ""},
                            {"input_delay", "additional input ns", "0"},
                            {"compute_delay", "additional compute ns", "0"},
                            {"return_delay", "additional return ns", "0"})
    IrValidation(SST::ComponentId_t id, SST::Params& p) : SST::Component(id) {
        if (getNumRanks().rank != 1 || getNumRanks().thread != 1)
            throw std::logic_error("IR requires one rank/thread");
        document_ = ir::parse(ir::read_json(p.find<std::string>("contract", "")));
        input_ = p.find<uint64_t>("input_delay", 0);
        compute_ = p.find<uint64_t>("compute_delay", 0);
        return_ = p.find<uint64_t>("return_delay", 0);
        link_ = configureSelfLink(
            "wake", "1ns", new SST::Event::Handler<IrValidation, &IrValidation::wake>(this));
        bridge_ = std::make_unique<EventBridge>(
            [this] {
                return getCurrentSimTimeNano();
            },
            [this](auto d, auto g) {
                ++pending_;
                link_->send(d, new IrWake(g));
            });
        runtime_ = std::make_unique<runtime::Runtime>(*bridge_, [this] {
            return getCurrentSimTimeNano();
        });
        registerAsPrimaryComponent();
        primaryComponentDoNotEndSim();
    }
    void setup() override {
        Simulator->Reset();
        Simulator->Initialize_simulation();
        bridge_->start();
        scenario_ = std::make_unique<ir::test::Scenario>(
            document_,
            *runtime_,
            *bridge_,
            [this] {
                return getCurrentSimTimeNano();
            },
            input_,
            compute_,
            return_);
    }
    void finish() override {
        scenario_->finish(65 + input_ + compute_ + return_);
        for (const auto& t : runtime_->transitions())
            std::cout << "IR_SST_STATE time_ns=" << t.time_ns << " query=" << t.query
                      << " task=" << t.task << " state=" << runtime::name(t.state) << "\n";
        if (runtime_->task_records() || runtime_->wait_records() || runtime_->private_slots())
            throw std::logic_error("IR SST records leaked");
        scenario_.reset();
        runtime_.reset();
        bridge_.reset();
        Simulator->Reset();
        std::cout << "V03-2 SST PASS terminal_tasks=0 terminal_waiters=0\n";
    }

private:
    void wake(SST::Event* raw) {
        std::unique_ptr<IrWake> e(static_cast<IrWake*>(raw));
        --pending_;
        try {
            bridge_->wake(e->generation);
            scenario_->executor.require_progress();
            if (!pending_ && bridge_->idle())
                primaryComponentOKToEndSim();
        } catch (const std::exception& ex) {
            getSimulationOutput().fatal(CALL_INFO, 1, "%s\n", ex.what());
        }
    }
    SST::Link* link_;
    unsigned pending_ = 0;
    uint64_t input_ = 0, compute_ = 0, return_ = 0;
    ir::Document document_;
    std::unique_ptr<EventBridge> bridge_;
    std::unique_ptr<runtime::Runtime> runtime_;
    std::unique_ptr<ir::test::Scenario> scenario_;
};
} // namespace ndpsim
