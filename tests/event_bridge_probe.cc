#include "sim/Engine.h"
#include <cstdio>
#include <stdexcept>
#include <vector>

void check(bool ok, const char* text) { if (!ok) throw std::logic_error(text); }
template<class F> void rejects(F action, const char* text) {
    try { action(); } catch (const std::logic_error&) { std::printf("PASS reject %s\n", text); return; }
    throw std::logic_error(text);
}
struct Probe : MQSimEngine::Sim_Object {
    Probe() : Sim_Object("probe") {}
    std::vector<int> seen;
    bool loop = false;
    void Validate_simulation_config() override {}
    void Start_simulation() override {
        Simulator->Register_sim_event(10, this, nullptr, 1);
        auto* ignored = Simulator->Register_sim_event(10, this, nullptr, 99);
        Simulator->Ignore_sim_event(ignored);
        Simulator->Register_sim_event(10, this, nullptr, 2);
    }
    void Execute_simulator_event(MQSimEngine::Sim_Event* ev) override {
        seen.push_back(ev->Type);
        if (ev->Type == 1) Simulator->Register_sim_event(10, this, nullptr, 3);
        if (loop) Simulator->Register_sim_event(Simulator->Time(), this, nullptr, 9);
    }
};
int main(int argc, char**) {
    try {
        Simulator->Reset();
        Probe probe;
        Simulator->AddObject(&probe);
        if (argc > 1) {
            Simulator->Start_simulation();
            check(probe.seen == std::vector<int>({1,2,3}), "native FIFO");
            check(Simulator->Time() == 10, "native final clock");
            Simulator->Reset();
            std::puts("PASS native Start_simulation FIFO");
            return 0;
        }
        rejects([] { Simulator->Synchronize_now(0); }, "before initialize");
        Simulator->Initialize_simulation();
        rejects([] { Simulator->Initialize_simulation(); }, "duplicate initialize");
        check(Simulator->Time() == 0 && Simulator->Peek_next_time() == 10, "init/peek consumed events");
        rejects([] { Simulator->Synchronize_now(11); }, "skipped pending event");
        check(Simulator->Time() == 0, "failed sync advanced clock");
        check(Simulator->Dispatch_current_time(9) == 0 && probe.seen.empty(), "future dispatch");
        rejects([] { Simulator->Synchronize_now(8); }, "time reversal");
        rejects([&] { Simulator->Register_sim_event(8, &probe); }, "past insertion");
        check(Simulator->Dispatch_current_time(10) == 3, "same-time drain");
        check(probe.seen == std::vector<int>({1,2,3}), "same-time FIFO");
        unsigned notifications = 0;
        Simulator->Set_event_queue_changed_callback([&] { ++notifications; });
        auto* ignored = Simulator->Register_sim_event(15, &probe, nullptr, 99);
        Simulator->Ignore_sim_event(ignored);
        check(!Simulator->Peek_next_time(), "ignored last event not removed");
        check(notifications == 2, "insert/ignore notification");
        Simulator->Synchronize_now(20);
        check(Simulator->Time() == 20, "empty interval synchronization");
        probe.loop = true;
        Simulator->Register_sim_event(20, &probe, nullptr, 9);
        rejects([] { Simulator->Dispatch_current_time(20, 4); }, "same-time livelock bound");
        check(probe.seen.size() == 7 && Simulator->Peek_next_time() == 20, "limit lost queued event");
        probe.loop = false;
        check(Simulator->Dispatch_current_time(20) == 1, "limit recovery");
        Simulator->Set_event_queue_changed_callback({});
        Simulator->Reset();
        std::puts("PASS stepping guards FIFO Ignore notifications livelock");
    } catch (const std::exception& error) { std::fprintf(stderr, "FAIL %s\n", error.what()); return 1; }
}
