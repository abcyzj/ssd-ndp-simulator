#pragma once
#include "dag/executor.h"
#include <sst/core/component.h>
#include <sst/core/link.h>
#include <sst/core/sst_config.h>
namespace ndpsim::dag {
struct HostInputs {
    std::filesystem::path package, scratch;
    Receipt receipt;
    Limits limits;
    Config config;
};
// Generic SST wiring. Concrete mapping modules implement lower() without adding
// DAG syntax, branch parsing or another clock to GraphHost.
class GraphHost : public SST::Component {
  public:
    GraphHost(SST::ComponentId_t, HostInputs);
    ~GraphHost() override;
    void setup() override;
    void finish() override;

  protected:
    virtual timing::Operation lower(const Stage &) = 0;
    virtual void on_request(const RequestRecord &) {}
    virtual void on_stage(const StageRecord &) {}
    virtual void on_drained(timing::Outcome) {}
    EventBridge &bridge();
    timing::Runtime &runtime();
    const Executor &executor() const;
    uint64_t drained_time() const;

  private:
    struct Data;
    std::unique_ptr<Data> data_;
    void wake(SST::Event *);
};
} // namespace ndpsim::dag
