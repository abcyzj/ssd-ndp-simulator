#pragma once
#include "dag/package.h"
#include "timing/runtime.h"
#include <optional>

namespace ndpsim::dag {
struct Config {
    uint64_t admission = 1, workers = 1, max_active_stages = 100000;
    uint64_t max_active_index_bytes = 64 * 1024 * 1024;
    std::vector<uint64_t> offered_ns; // One per original manifest query; default all zero.
};
struct RequestRecord {
    uint64_t query, offered, admitted, worker_start, visible, drained;
    bool success;
};
struct StageRecord {
    capture::Key key;
    uint64_t submitted, completed;
    bool success;
};
struct Metrics {
    uint64_t admitted = 0, completed = 0, skipped = 0;
    uint64_t active_queries = 0, active_workers = 0, active_stages = 0, active_index_bytes = 0;
    uint64_t peak_queries = 0, peak_workers = 0, peak_stages = 0, peak_index_bytes = 0;
    uint64_t outstanding = 0, ready_edges = 0;
};
using Lower = std::function<timing::Operation(const Stage &)>;
// No file-order replay, functional operator, cost formula or hardware mapping.
// Lower supplies explicit services; only a Visible dependency permits its call.
class Executor {
  public:
    Executor(Package &, timing::Runtime &, EventBridge &, EventBridge::Clock, Config, Lower);
    ~Executor();
    Executor(const Executor &) = delete;
    Executor &operator=(const Executor &) = delete;
    void start(timing::Done);
    void require_progress() const;
    bool drained() const;
    const Metrics &metrics() const;
    History<RequestRecord> &requests();
    History<StageRecord> &stages();

  private:
    struct Data;
    std::unique_ptr<Data> data_;
};
} // namespace ndpsim::dag
