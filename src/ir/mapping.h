#pragma once
#include "ir/executor.h"
#include "model/controller.h"
#include "model/host_cpu.h"

namespace ndpsim::ir {
// Trusted backend boundary. The frontend receives leases, never these capabilities.
// All owners and their Runtime must outlive pending callbacks.
class Mapping {
public:
    using Read = std::function<runtime::Handle(uint64_t, const content::Selection&)>;
    using Bind = std::function<void(uint64_t, uint64_t)>; // IR parent, Runtime child
    struct ObjectBinding {
        std::string object_id;
        uint64_t dataset, version;
        content::Kind kind;
    };
    Mapping(Document,
            Executor&,
            runtime::Runtime&,
            model::HostCpu&,
            const content::ContentStore&,
            const content::Layout&,
            Read,
            std::vector<ObjectBinding>,
            model::Controller* = nullptr,
            Bind = {});
    // Called at original query admission; controller upload occurs once here.
    std::function<void(uint64_t, const char*, uint64_t)> session_bind;
    void admit(uint64_t query, std::vector<float>, std::shared_ptr<const StateSnapshot>);
    Lease submit(Operation, std::vector<Lease>, States = {});
    // Drains pending mapping operations, then the original controller Close/Ack.
    runtime::Handle close(uint64_t query);
    size_t sessions() const {
        return queries_.size();
    }

private:
    struct Query {
        std::vector<float> vector;
        std::shared_ptr<const StateSnapshot> state;
        bool ready = false, closing = false;
        std::map<uint64_t, Lease> active;
        std::optional<runtime::Failure> failure;
    };
    Document contract_;
    Executor& executor_;
    runtime::Runtime& runtime_;
    model::HostCpu& cpu_;
    const content::ContentStore& store_;
    const content::Layout& layout_;
    Read read_;
    std::map<std::string, ObjectBinding> objects_;
    model::Controller* controller_;
    Bind bind_;
    std::map<uint64_t, Query> queries_;
    std::vector<content::Selection> selections(const Operation&, const Values&) const;
    operators::Result control(const Operation&, const Values&) const;
    std::vector<runtime::Handle>
    dispatch(const Operation&, const Values&, std::vector<runtime::Handle>& spawned);
};
} // namespace ndpsim::ir
