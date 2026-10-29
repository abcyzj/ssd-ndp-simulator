#include <sst/core/sst_config.h>
#include <sst/core/component.h>
#include <sst/core/link.h>
#include "backend/lifecycle.h"
#include "sim/Engine.h"
#include <cstdio>
#include <memory>
#include <optional>
#include <stdexcept>
#include <utility>
#include <vector>

namespace ndpsim {
namespace {
void require(bool ok, const char* message) {
    if (!ok)
        throw std::logic_error(message);
}
class Wake : public SST::Event {
public:
    Wake(uint64_t generation, bool external = false) : generation(generation), external(external) {
    }
    uint64_t generation;
    bool external;
};
class Fixture : public MQSimEngine::Sim_Object {
public:
    Fixture() : Sim_Object("event-bridge-fixture") {
    }
    std::function<void(int)> execute;
    void Validate_simulation_config() override {
    }
    void Start_simulation() override {
        Simulator->Register_sim_event(10, this, nullptr, 1);
        Simulator->Register_sim_event(10, this, nullptr, 2);
        auto* ignored = Simulator->Register_sim_event(15, this, nullptr, 99);
        Simulator->Ignore_sim_event(ignored);
        Simulator->Register_sim_event(30, this, nullptr, 8);
    }
    void Execute_simulator_event(MQSimEngine::Sim_Event* event) override {
        execute(event->Type);
        if (event->Type == 1)
            Simulator->Register_sim_event(10, this, nullptr, 3);
        if (event->Type == 3)
            Simulator->Register_sim_event(10, this, nullptr, 4);
        if (event->Type == 5)
            Simulator->Register_sim_event(20, this, nullptr, 6);
    }
};
} // namespace
// Deliberately separate from System: E1 deterministic callback fixture, no I/O.
class BridgePoC : public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(BridgePoC,
                               "ndpsim",
                               "BridgePoC",
                               SST_ELI_ELEMENT_VERSION(0, 1, 0),
                               "M0.3a event bridge PoC; no I/O or algorithm runtime",
                               COMPONENT_CATEGORY_UNCATEGORIZED)
    BridgePoC(SST::ComponentId_t id, SST::Params& params) : SST::Component(id) {
        try {
            require(getNumRanks().rank == 1 && getNumRanks().thread == 1,
                    "PoC needs one rank/thread");
            require(params.getKeys().empty(), "PoC has no configurable parameters");
            backend_ = std::make_unique<LifecycleBackend>();
            fixture_.execute = [this](int type) {
                const auto now = getCurrentSimTimeNano();
                require(Simulator->Time() == now, "backend executed beyond SST now");
                seen_.emplace_back(now, type);
                std::printf("E1 backend SST=%llu MQ=%llu type=%d\n",
                            (unsigned long long)now,
                            (unsigned long long)Simulator->Time(),
                            type);
            };
            Simulator->AddObject(&fixture_);
            link_ = configureSelfLink(
                "bridge", "1ns", new SST::Event::Handler<BridgePoC, &BridgePoC::onWake>(this));
            require(link_ != nullptr, "cannot create bridge SelfLink");
            registerAsPrimaryComponent();
            primaryComponentDoNotEndSim();
        } catch (const std::exception& error) {
            fail(error);
        }
    }
    void setup() override {
        try {
            backend_->initialize();
            require(Simulator->Time() == 0, "initialization consumed events");
            require(Simulator->Peek_next_time() == 10, "initial peek must be 10 ns");
            // Queue callbacks during a backend phase are coalesced until it stabilizes.
            Simulator->Set_event_queue_changed_callback([this] {
                if (!in_phase_)
                    arm();
            });
            arm();
            link_->send(20, new Wake(0, true));
            ++pending_;
        } catch (const std::exception& error) {
            fail(error);
        }
    }
    void finish() override {
        try {
            const std::vector<std::pair<uint64_t, int>> expected{
                {10, 1}, {10, 2}, {10, 3}, {10, 4}, {20, 5}, {20, 6}, {25, 7}, {30, 8}};
            require(seen_ == expected, "E1 event sequence mismatch");
            require(injected_ && stale_ >= 2 && !Simulator->Peek_next_time() && pending_ == 0,
                    "E1 missing injection/stale wake/empty queue coverage");
            Simulator->Set_event_queue_changed_callback({});
            backend_->finish();
            std::printf(
                "E1 PASS events=8 injection=20 earlier=25 ignored=15,22 stale=%u final=30 ns\n",
                stale_);
        } catch (const std::exception& error) {
            fail(error);
        }
    }

private:
    void arm() {
        const auto next = Simulator->Peek_next_time();
        if (next == reserved_)
            return;
        reserved_ = next;
        ++generation_; // Old events remain in SST and are rejected on delivery.
        if (next) {
            const auto now = getCurrentSimTimeNano();
            require(*next >= now, "attempt to reserve past wake");
            link_->send(*next - now, new Wake(generation_));
            ++pending_;
            std::printf("E1 reserve now=%llu target=%llu generation=%llu\n",
                        (unsigned long long)now,
                        (unsigned long long)*next,
                        (unsigned long long)generation_);
        }
    }
    void onWake(SST::Event* raw) {
        std::unique_ptr<Wake> event(static_cast<Wake*>(raw));
        try {
            --pending_;
            const auto now = getCurrentSimTimeNano();
            if (!event->external && event->generation != generation_) {
                ++stale_;
                std::printf("E1 stale SST=%llu generation=%llu backend=%llu\n",
                            (unsigned long long)now,
                            (unsigned long long)event->generation,
                            (unsigned long long)Simulator->Time());
            } else {
                if (!event->external)
                    reserved_.reset();
                in_phase_ = true;
                // Existing backend events precede external injection at the same ns.
                Simulator->Dispatch_current_time(now);
                if (event->external) {
                    require(now == 20 && Simulator->Time() == 20,
                            "injection has stale backend clock");
                    require(Simulator->Peek_next_time() == 30,
                            "backend crossed external injection");
                    injected_ = true;
                    std::puts("E1 inject SST=20 MQ=20 next=30");
                    // Outside dispatch, exercise notifications and invalidated reservations.
                    in_phase_ = false;
                    Simulator->Register_sim_event(25, &fixture_, nullptr, 7);
                    auto* ignored = Simulator->Register_sim_event(22, &fixture_, nullptr, 99);
                    Simulator->Ignore_sim_event(ignored);
                    in_phase_ = true;
                    Simulator->Register_sim_event(20, &fixture_, nullptr, 5);
                    Simulator->Dispatch_current_time(now); // next delta, no artificial ns
                }
                in_phase_ = false;
                arm();
            }
            if (pending_ == 0 && !Simulator->Peek_next_time())
                primaryComponentOKToEndSim();
        } catch (const std::exception& error) {
            fail(error);
        }
    }
    void fail(const std::exception& error) {
        getSimulationOutput().fatal(CALL_INFO, 1, "E1 bridge error: %s\n", error.what());
    }
    std::unique_ptr<LifecycleBackend> backend_;
    Fixture fixture_;
    SST::Link* link_ = nullptr;
    std::optional<uint64_t> reserved_;
    uint64_t generation_ = 0;
    unsigned pending_ = 0, stale_ = 0;
    bool in_phase_ = false, injected_ = false;
    std::vector<std::pair<uint64_t, int>> seen_;
};
} // namespace ndpsim
