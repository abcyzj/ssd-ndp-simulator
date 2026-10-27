#include <sst/core/sst_config.h>
#include <sst/core/component.h>
#include <sst/core/link.h>
#include "backend/lifecycle.h"
#include <cstdio>
#include <memory>
#include <stdexcept>

namespace ndpsim {
class System : public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(System, "ndpsim", "System", SST_ELI_ELEMENT_VERSION(0, 1, 0),
        "Single-device MQSim lifecycle skeleton (no I/O bridge)", COMPONENT_CATEGORY_UNCATEGORIZED)
    SST_ELI_DOCUMENT_PARAMS(
        {"device_count", "Must be 1", "1"},
        {"profile", "M1.1 supports native-small lifecycle profile only", "native-small"})

    System(SST::ComponentId_t id, SST::Params& params) : SST::Component(id) {
        try {
            if (getNumRanks().rank != 1 || getNumRanks().thread != 1)
                throw std::invalid_argument("Unsupported: requires one rank and one thread");
            for (const auto& key : params.getKeys())
                if (key != "device_count" && key != "profile")
                    throw std::invalid_argument("Unknown ndpsim.System parameter: " + key);
            const auto count = params.find<std::string>("device_count", "1");
            if (count != "1") throw std::invalid_argument("Unsupported: device_count must be 1");
            backend_ = std::make_unique<LifecycleBackend>(1, params.find<std::string>("profile", "native-small"));
            registerAsPrimaryComponent();
            primaryComponentDoNotEndSim();
            wake_ = configureSelfLink("lifecycle", "1ns", new SST::Event::Handler<System, &System::onWake>(this));
            if (!wake_) throw std::runtime_error("Cannot configure lifecycle SelfLink");
            std::puts("ndpsim: constructed single MQSim device; lifecycle only");
        } catch (const std::exception& error) { fail(error); }
    }

    void init(unsigned phase) override {
        if (phase == 0) std::puts("ndpsim: init phase 0");
    }
    void setup() override {
        try {
            backend_->initialize();
            std::puts("ndpsim: setup initialized backend at 0 ns; no workload");
            wake_->send(1, new SST::Event());
        } catch (const std::exception& error) { fail(error); }
    }
    void finish() override {
        try {
            if (!completed_) throw std::logic_error("Lifecycle completion event was not delivered");
            backend_->finish();
            std::printf("ndpsim: finish SST=%llu ns MQSim=0 ns; device destroyed\n",
                static_cast<unsigned long long>(getCurrentSimTimeNano()));
        } catch (const std::exception& error) { fail(error); }
    }
private:
    void onWake(SST::Event* event) {
        std::unique_ptr<SST::Event> owner(event);
        if (backend_->time_ns() != 0) {
            std::logic_error error("M1.1 advanced backend time"); fail(error);
        }
        completed_ = true;
        primaryComponentOKToEndSim();
    }
    void fail(const std::exception& error) {
        getSimulationOutput().fatal(CALL_INFO, 1, "ndpsim configuration/lifecycle error: %s\n", error.what());
    }
    std::unique_ptr<LifecycleBackend> backend_;
    SST::Link* wake_ = nullptr;
    bool completed_ = false;
};
}
