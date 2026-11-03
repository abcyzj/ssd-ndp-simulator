#include "ir/executor.h"
#include <algorithm>
#include <cmath>
#include <set>
#include <sstream>

namespace ndpsim::ir {
namespace {
content::Error adapter_error(const Operation& op,
                             uint64_t now,
                             const std::exception& cause,
                             content::Code code = content::Code::BackendFailure) {
    Error e(content::name(code), "/adapter", cause.what(), op.scope.query, op.op_id);
    e.time_ns = now;
    return content::Error(code, e.diagnostic().dump());
}
} // namespace
std::shared_ptr<const StateSnapshot>
StateSnapshot::initial(uint64_t query, std::string id, Json data) {
    if (!query || id.empty())
        throw Error("InvalidArgument", "/state", "invalid state identity");
    auto s = std::shared_ptr<StateSnapshot>(new StateSnapshot);
    s->identity_ = {query, std::move(id), 1};
    s->data_ = std::move(data);
    s->sequence_ = std::make_shared<uint64_t>(1);
    return s;
}
std::shared_ptr<const StateSnapshot>
StateSnapshot::advance(std::shared_ptr<const StateSnapshot> old, Json data) {
    if (!old || *old->sequence_ == UINT64_MAX)
        throw Error("Overflow", "/state", "invalid/overflow state lineage");
    auto s = std::shared_ptr<StateSnapshot>(new StateSnapshot);
    s->identity_ = old->identity_;
    s->identity_.version = ++*old->sequence_;
    s->sequence_ = old->sequence_;
    s->data_ = std::move(data);
    return s;
}
Executor::Executor(Document contract, runtime::Runtime& runtime, std::function<uint64_t()> now)
    : contract_(std::move(contract)), runtime_(runtime), now_(std::move(now)) {
    if (!contract_.operations.empty())
        throw Error("InvalidArgument", "/operations", "executor requires an empty run contract");
    validate(contract_);
    for (unsigned i = 0; i < capabilities().at("operators").size(); ++i)
        operator_counts.emplace(static_cast<Operator>(i), Counts{});
}
[[noreturn]] void Executor::error(const Operation& op,
                                  std::string code,
                                  std::string detail,
                                  uint64_t producer) const {
    Error e(std::move(code),
            "/execution",
            "time_ns=" + std::to_string(now_()) + " " + detail,
            op.scope.query,
            op.op_id,
            producer);
    e.time_ns = now_();
    throw e;
}
const Executor::Record& Executor::checked(Lease h) const {
    if (h.owner_ != this || !h.active_)
        throw Error("InvalidHandle", "/lease", "foreign/invalid IR lease");
    if (!*h.active_)
        throw Error("Released", "/lease", "released IR lease", 0, h.op_);
    auto it = records_.find(h.op_);
    if (it == records_.end())
        throw Error("InvalidHandle", "/lease", "reclaimed IR operation", 0, h.op_);
    return it->second;
}
Lease Executor::retain(Lease h) {
    checked(h);
    ++records_.at(h.op_).owners;
    h.active_ = std::make_shared<bool>(true);
    return h;
}
void Executor::collect(uint64_t id) {
    auto it = records_.find(id);
    if (it != records_.end() && !it->second.owners && !it->second.pins) {
        if (observe)
            observe(it->second.op, "reclaimed", it->second.completion.id(), {});
        runtime_.release(it->second.completion);
        counts_.active_values -= it->second.op.outputs.size();
        records_.erase(it);
        counts_.active_ops = records_.size();
    }
}
void Executor::release(Lease h) {
    checked(h);
    *h.active_ = false;
    --records_.at(h.op_).owners;
    collect(h.op_);
}
Value Executor::get(Lease h, const std::string& port) const {
    auto& r = checked(h);
    (void)runtime_.try_get(r.completion);
    auto it = r.outputs.find(port);
    if (it == r.outputs.end())
        error(r.op, "InvalidArgument", "unknown output port " + port);
    return it->second;
}
Values Executor::resolve(const Operation& op, const States& snapshots) const {
    auto output = [&](uint64_t id, const std::string& port) {
        const auto& r = records_.at(id);
        (void)runtime_.try_get(r.completion);
        return r.outputs.at(port);
    };
    Values values;
    for (const auto& input : op.inputs) {
        const auto& v = input.value;
        Value value;
        if (auto ref = std::get_if<OutputRef>(&v.reference))
            value = output(ref->producer_op, ref->port);
        else if (auto literal = std::get_if<Literal>(&v.reference))
            std::visit(
                [&](const auto& x) {
                    value.data = x;
                },
                literal->value);
        else if (auto slice = std::get_if<Slice>(&v.reference)) {
            if (auto ids = std::get_if<std::vector<uint32_t>>(&slice->indices))
                value.data = *ids;
            else {
                const auto& ref = std::get<OutputRef>(slice->indices);
                value = output(ref.producer_op, ref.port);
            }
        } else {
            const auto& ref = std::get<StateRef>(v.reference);
            bool found = false;
            if (auto it = snapshots.find(input.port); it != snapshots.end()) {
                value.state = it->second;
                found = true;
            }
            for (const auto& dep : op.deps)
                if (dep.kind == DependencyKind::Order && dep.state_version == ref.version) {
                    auto candidate = output(dep.producer_op, dep.port);
                    if (candidate.state) {
                        const auto& s = candidate.state->identity();
                        if (s.query == ref.query && s.state_id == ref.state_id &&
                            s.version == ref.version) {
                            if (found)
                                error(op,
                                      "VersionMismatch",
                                      "ambiguous state binding",
                                      dep.producer_op);
                            found = true;
                            value = std::move(candidate);
                        }
                    }
                }
            if (!found)
                error(op,
                      "VersionMismatch",
                      "state identity/version is not bound to ordered visible output");
        }
        values.emplace(input.port, std::move(value));
    }
    // Whole address batch checked before granting ANY real resource/credit.
    for (const auto& a : op.access) {
        Json ids;
        if (auto static_ids = std::get_if<std::vector<uint32_t>>(&a.indices))
            ids = *static_ids;
        else {
            const auto& r = std::get<OutputRef>(a.indices);
            ids = output(r.producer_op, r.port).data;
        }
        auto object =
            std::find_if(contract_.objects.begin(), contract_.objects.end(), [&](const auto& o) {
                return o.object_id == a.object_id;
            });
        if (!ids.is_array())
            error(op, "InvalidArgument", "address output is not an ID array");
        if (op.op == Operator::ReadAdjacency && ids.empty())
            error(op, "InvalidArgument", "empty SELECT must terminate without adjacency reads");
        for (const auto& id : ids)
            if (!id.is_number_unsigned() || id.get<uint64_t>() >= object->element_count)
                error(op, "OutOfRange", "dynamic address outside logical object");
    }
    return values;
}
void Executor::validate_values(const Operation& op,
                               const Values& values,
                               const Values& inputs) const {
    if (values.size() != op.outputs.size())
        error(op, "InvalidArgument", "output port count mismatch");
    for (const auto& port : op.outputs) {
        auto it = values.find(port.name);
        if (it == values.end())
            error(op, "InvalidArgument", "missing output " + port.name);
        const auto& v = it->second;
        auto bad = [&] {
            error(op, "InvalidArgument", "invalid actual type/shape for " + port.name);
        };
        if (port.type.name == TypeName::QueryState) {
            if (!v.state || v.state->identity().query != op.scope.query)
                bad();
            continue;
        }
        if (v.state)
            bad();
        const auto& d = v.data;
        if (!port.type.shape.empty()) {
            const auto& dim = port.type.shape[0];
            if (!d.is_array() || (dim.dynamic ? d.size() > dim.extent : d.size() != dim.extent))
                bad();
        }
        auto u32 = [](const Json& x) {
            return x.is_number_unsigned() && x.get<uint64_t>() <= UINT32_MAX;
        };
        switch (port.type.name) {
        case TypeName::Token:
            if (!d.is_null())
                bad();
            break;
        case TypeName::Bool:
            if (!d.is_boolean())
                bad();
            break;
        case TypeName::U32:
            if (!u32(d))
                bad();
            break;
        case TypeName::U64:
            if (!d.is_number_unsigned())
                bad();
            break;
        case TypeName::IdsU32:
            for (const auto& x : d)
                if (!u32(x))
                    bad();
            break;
        case TypeName::Fp32:
            for (const auto& x : d)
                if (!x.is_number() || !std::isfinite(x.get<double>()) ||
                    !std::isfinite(static_cast<float>(x.get<double>())))
                    bad();
            break;
        case TypeName::ScoresIdF64:
            for (const auto& x : d)
                if (!x.is_object() || x.size() != 2 || !x.contains("id") ||
                    !x.contains("distance") || !u32(x["id"]) || !x["distance"].is_number() ||
                    !std::isfinite(x["distance"].get<double>()))
                    bad();
            break;
        case TypeName::Adjacency:
            for (const auto& x : d) {
                if (!x.is_object() || x.size() != 2 || !x.contains("degree") ||
                    !x.contains("ids") || !u32(x["degree"]) || !x["ids"].is_array() ||
                    x["degree"].get<uint64_t>() != x["ids"].size())
                    bad();
                for (const auto& id : x["ids"])
                    if (!u32(id))
                        bad();
            }
            break;
        case TypeName::QueryState:
            break;
        }
    }
    if ((op.op == Operator::ReadAdjacency &&
         values.at("adjacency").data.size() != inputs.at("ids").data.size()) ||
        (op.op == Operator::ScoreL2 &&
         values.at("scores").data.size() != inputs.at("ids").data.size()))
        error(op, "InvalidArgument", "actual output cardinality differs from address batch");
}
Lease Executor::submit(Operation op,
                       std::vector<Lease> producers,
                       Adapter adapter,
                       States snapshots) {
    if (!op.op_id || op.op_id <= last_id_ || op.op_id == UINT64_MAX)
        error(op, "InvalidArgument", "nonmonotonic/reused/overflow op ID");
    Document context = contract_;
    std::set<uint64_t> provided, required;
    for (auto lease : producers) {
        const auto& r = checked(lease);
        if (!provided.insert(lease.op_).second)
            error(op, "InvalidArgument", "duplicate producer lease");
        context.operations.push_back(r.op);
    }
    std::sort(
        context.operations.begin(), context.operations.end(), [](const auto& a, const auto& b) {
            return a.op_id < b.op_id;
        });
    try {
        validate_submission(context, op);
    } catch (Error& e) {
        e.time_ns = now_();
        throw;
    }
    for (const auto& dep : op.deps)
        required.insert(dep.producer_op);
    if (required != provided)
        error(op, "InvalidHandle", "each dependency must have a live producer lease");
    std::set<std::string> state_ports;
    for (const auto& input : op.inputs)
        if (auto state = std::get_if<StateRef>(&input.value.reference)) {
            state_ports.insert(input.port);
            bool bound = false;
            if (auto it = snapshots.find(input.port); it != snapshots.end()) {
                if (!it->second)
                    error(op, "InvalidHandle", "null state snapshot");
                const auto& identity = it->second->identity();
                if (identity.query != state->query || identity.state_id != state->state_id ||
                    identity.version != state->version)
                    error(op, "VersionMismatch", "state snapshot identity/version mismatch");
                bound = true;
            }
            for (const auto& dep : op.deps)
                if (dep.kind == DependencyKind::Order && dep.state_version == state->version)
                    bound = true;
            if (!bound)
                error(op,
                      "VersionMismatch",
                      "state_ref requires an owned snapshot or explicit ORDER/version binding");
        }
    for (const auto& [port, snapshot] : snapshots) {
        (void)snapshot;
        if (!state_ports.count(port))
            error(op, "InvalidArgument", "unused state snapshot binding");
    }
    if (op.op == Operator::ReadAdjacency || op.op == Operator::ScoreL2) {
        bool control = false;
        for (const auto& dep : op.deps)
            if (dep.kind == DependencyKind::Control)
                control = true;
        if (!control)
            error(op, "InvalidArgument", "logical read requires explicit CONTROL permission");
    }
    if ((!adapter.grant && !adapter.launch) || !adapter.evaluate || !adapter.service ||
        (adapter.needs_return && !adapter.send_return))
        error(op, "Unsupported", "no trusted execution adapter");
    auto id = op.op_id;
    runtime::Operation task;
    task.query = op.scope.query;
    task.needs_return = adapter.needs_return;
    for (auto producer : provided)
        task.inputs.push_back(records_.at(producer).completion);
    auto resolved = std::make_shared<Values>();
    auto states = std::make_shared<States>(std::move(snapshots));
    task.grant = [this,
                  id,
                  resolved,
                  states,
                  grant = std::move(adapter.grant),
                  launch = std::move(adapter.launch)](uint64_t task_id) {
        try {
            *resolved = resolve(records_.at(id).op, *states);
            states->clear();
        } catch (const Error& e) {
            states->clear();
            resolved->clear();
            auto code = content::Code::BackendFailure;
            for (unsigned i = 0; i <= static_cast<unsigned>(content::Code::CapacityExceeded); ++i)
                if (e.code == content::name(static_cast<content::Code>(i)))
                    code = static_cast<content::Code>(i);
            throw content::Error(code, e.diagnostic().dump());
        }
        try {
            if (launch)
                launch(task_id, *resolved);
            else
                grant(task_id);
        } catch (const content::Error& e) {
            throw adapter_error(records_.at(id).op, now_(), e, e.code);
        } catch (const std::exception& e) {
            throw adapter_error(records_.at(id).op, now_(), e);
        }
    };
    task.evaluate = [this, id, resolved, evaluate = std::move(adapter.evaluate)](const auto&) {
        try {
            auto values = evaluate(*resolved);
            validate_values(records_.at(id).op, values, *resolved);
            records_.at(id).outputs = std::move(values);
            resolved->clear();
            // Runtime gates visibility/completion without making a second payload copy.
            return content::Bytes{};
        } catch (const Error& e) {
            throw content::Error(content::Code::InvalidArgument, e.diagnostic().dump());
        } catch (const content::Error& e) {
            throw adapter_error(records_.at(id).op, now_(), e, e.code);
        } catch (const std::exception& e) {
            throw adapter_error(records_.at(id).op, now_(), e);
        }
    };
    task.service = [this, id, service = std::move(adapter.service)](uint64_t task_id) {
        try {
            service(task_id);
        } catch (const content::Error& e) {
            throw adapter_error(records_.at(id).op, now_(), e, e.code);
        } catch (const std::exception& e) {
            throw adapter_error(records_.at(id).op, now_(), e);
        }
    };
    // Adapter owns the real payload accounting; Runtime's gate carries no bytes.
    task.send_return = [this, id, send = std::move(adapter.send_return)](uint64_t task_id, size_t) {
        try {
            send(task_id);
        } catch (const content::Error& e) {
            throw adapter_error(records_.at(id).op, now_(), e, e.code);
        } catch (const std::exception& e) {
            throw adapter_error(records_.at(id).op, now_(), e);
        }
    };
    Record record{std::move(op), {}, 1, 1, {}, {provided.begin(), provided.end()}};
    record.completion = runtime_.submit(std::move(task));
    records_.emplace(id, std::move(record));
    for (auto producer : provided)
        ++records_.at(producer).pins;
    last_id_ = id;
    ++counts_.submitted;
    ++operator_counts[records_.at(id).op.op].submitted;
    if (observe)
        observe(records_.at(id).op, "submitted", records_.at(id).completion.id(), {});
    counts_.active_ops = records_.size();
    counts_.peak_ops = std::max(counts_.peak_ops, counts_.active_ops);
    counts_.active_values += records_.at(id).op.outputs.size();
    counts_.peak_values = std::max(counts_.peak_values, counts_.active_values);
    runtime_.when_all({records_.at(id).completion}, id, [this, id](runtime::Outcome outcome) {
        auto& record = records_.at(id);
        if (outcome.failure) {
            ++counts_.failed;
            ++operator_counts[record.op.op].failed;
            record.outputs.clear();
        } else {
            ++counts_.completed;
            ++operator_counts[record.op.op].completed;
        }
        if (observe)
            observe(record.op,
                    outcome.failure ? "failed" : "visible",
                    record.completion.id(),
                    record.outputs);
        for (auto producer : record.producers) {
            --records_.at(producer).pins;
            collect(producer);
        }
        record.producers.clear();
        --record.pins;
        collect(id);
    });
    Lease lease;
    lease.owner_ = this;
    lease.op_ = id;
    lease.active_ = std::make_shared<bool>(true);
    return lease;
}
Lease Executor::join(Operation op, std::vector<Lease> producers) {
    if (op.op != Operator::Join)
        error(op, "InvalidArgument", "join requires SYNC/JOIN");
    Adapter a;
    a.grant = [this](auto id) {
        runtime_.start(id);
    };
    a.evaluate = [](const Values&) {
        return Values{{"done", {nullptr, {}}}};
    };
    a.service = [this](auto id) {
        runtime_.service_done(id);
    };
    return submit(std::move(op), std::move(producers), std::move(a));
}
void Executor::when_all(std::vector<Lease> leases,
                        uint64_t continuation,
                        std::function<void(runtime::Outcome)> callback) {
    if (!continuation || !callback)
        throw Error("InvalidArgument", "/continuation", "invalid continuation");
    std::vector<runtime::Handle> handles;
    for (auto lease : leases)
        handles.push_back(checked(lease).completion);
    for (auto lease : leases)
        ++records_.at(lease.op_).pins;
    runtime_.when_all(std::move(handles),
                      continuation,
                      [this, leases = std::move(leases), callback = std::move(callback)](
                          runtime::Outcome outcome) {
                          try {
                              callback(outcome);
                          } catch (...) {
                              for (auto lease : leases) {
                                  --records_.at(lease.op_).pins;
                                  collect(lease.op_);
                              }
                              throw;
                          }
                          for (auto lease : leases) {
                              --records_.at(lease.op_).pins;
                              collect(lease.op_);
                          }
                      });
}
void Executor::fail_after_drain(uint64_t id, content::Code code, std::string detail) {
    auto it = records_.find(id);
    if (it == records_.end())
        throw Error("InvalidHandle", "/failure", "unknown/reclaimed IR operation", 0, id);
    const auto& op = it->second.op;
    Error e(content::name(code), "/adapter", std::move(detail), op.scope.query, id);
    e.time_ns = now_();
    runtime_.fail_after_drain(it->second.completion.id(), code, e.diagnostic().dump());
}
void Executor::require_progress() const {
    try {
        runtime_.require_progress();
    } catch (const std::logic_error& e) {
        throw std::logic_error(std::string(e.what()) + " IR:" + diagnostic());
    }
}
std::string Executor::diagnostic() const {
    std::ostringstream out;
    for (const auto& [id, r] : records_) {
        out << " query=" << r.op.scope.query << " ir_op=" << id << " task=" << r.completion.id()
            << " owners=" << r.owners << " pins=" << r.pins << " dependencies=";
        for (const auto& d : r.op.deps)
            out << d.producer_op << ":" << d.port << ",";
    }
    return out.str() + runtime_.diagnostic();
}
} // namespace ndpsim::ir
