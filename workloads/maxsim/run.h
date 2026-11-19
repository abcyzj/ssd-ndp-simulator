#pragma once
#include "backend/event_bridge.h"
#include <filesystem>
#include <memory>

namespace maxsim {
class Run {
public:
    Run(const std::filesystem::path&, ndpsim::EventBridge&, ndpsim::EventBridge::Clock);
    ~Run();
    void start();
    void poll();
    bool finished() const;
    void verify() const;

private:
    struct State;
    std::unique_ptr<State> state_;
};
}
