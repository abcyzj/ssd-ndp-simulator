#pragma once
#include "timing/runtime.h"
#include "model/cost/cost_model.h"
#include "timing/messages.h"
#include "timing/local_reads.h"
namespace ndpsim::timing {
struct ControllerResources {
    uint64_t capacity_bytes = 16384, query_bound = 128, block_bound = 4096;
    uint64_t admission = 1, staging_bytes_per_ns = 16, compute_slots = 1;
};
struct ControllerRequest {
    Message command, result;
    layout::Plan reads;
    uint64_t scratch_bytes = 0, scratch_access_bytes = 0;
    Operation operation; // Explicit P05 demand, Work, output metadata and provenance key.
};
// Generic service endpoint: mapping supplies complete physical blocks and demand.
// No L2, payload, Work splitting, graph traversal or second time authority.
class MetadataController {
public:
    struct BufferAccess {
        std::string key;
        uint64_t bytes, submitted, duration_ns;
    };
    const std::vector<BufferAccess>& buffer_accesses() const {
        return accesses_;
    }
    History<BufferAccess>& observations() {
        return accesses_;
    }
    MetadataController(Runtime&,
                       MetadataLocalReads&,
                       MetadataPcie&,
                       const MessageRules&,
                       EventBridge&,
                       EventBridge::Clock,
                       ControllerResources);
    CompletionToken open(Message);
    CompletionToken execute(ControllerRequest);
    CompletionToken execute_profile(ControllerRequest, const cost::Profile&, const cost::Input&);
    CompletionToken close(uint64_t query);
    void fail(CompletionToken, std::string detail);
    size_t sessions() const {
        return sessions_.size();
    }
    size_t pending_jobs() const {
        return jobs_.size();
    }
    size_t pending_protocol() const {
        return conversation_.pending();
    }
    const BufferModel& buffer() const {
        return buffer_;
    }
    const FifoResource& port() const {
        return port_;
    }
    const FifoResource& compute() const {
        return compute_;
    }
    History<CreditRecord>& credit_observations() { return buffer_.observations(); }
    History<ServiceRecord>& compute_observations() { return compute_.observations(); }
    History<ServiceRecord>& port_observations() { return port_.observations(); }
    // Test/research boundary observer, no implicit target service or clock charge.
    std::function<void(const std::string&, uint64_t)> observe;

private:
    struct Session {
        Message open;
        CompletionToken token;
        uint64_t credit = 0, query_bytes = 0, outstanding = 0;
        bool closing = false, failed = false;
        std::set<uint64_t> chunks;
        std::function<void()> drained;
    };
    struct Job {
        ControllerRequest request;
        std::shared_ptr<Session> session;
        CompletionToken token;
        Done done;
        uint64_t ticket = 0, credit = 0;
        bool sent = false, replied = false, input_released = false;
        std::string error;
    };
    Runtime& runtime_;
    MetadataLocalReads& reads_;
    MetadataPcie& transport_;
    const MessageRules& rules_;
    EventBridge::Clock now_;
    ControllerResources config_;
    BufferModel buffer_;
    FifoResource port_, compute_, endpoint_;
    Conversation conversation_;
    History<BufferAccess> accesses_;
    std::map<uint64_t, std::shared_ptr<Session>> sessions_;
    std::map<uint64_t, std::shared_ptr<Job>> jobs_;
    uint64_t next_task_ = 0, next_token_ = 0;
    CompletedIds retired_;
    uint64_t task();
    void send(Message, std::function<void()>);
    void stage(std::string, uint64_t, std::function<void()>);
    void boundary(const std::shared_ptr<Job>&, const std::string&, std::function<void()>);
    void stage_job(const std::shared_ptr<Job>&, std::string, uint64_t, std::function<void()>);
    void begin_job(const std::shared_ptr<Job>&);
    void input_ready(const std::shared_ptr<Job>&);
    void compute_ready(const std::shared_ptr<Job>&);
    void computed(const std::shared_ptr<Job>&);
    void result_ready(const std::shared_ptr<Job>&);
    void release_input(const std::shared_ptr<Job>&);
    void reply(const std::shared_ptr<Job>&);
};
} // namespace ndpsim::timing
