#include "ir/mapping.h"
#include "search/graph.h"
#include "search/core.h"
#include <algorithm>
#include <cstring>
#include <set>

namespace ndpsim::ir {
namespace {
using content::Bytes;
void require(bool ok, const char* detail) {
    if (!ok)
        throw content::Error(content::Code::InvalidArgument, detail);
}
Bytes packed(const Value& value) {
    if (auto bytes = value.packed_scores())
        return *bytes;
    const auto& scores = value.data();
    Bytes bytes;
    bytes.reserve(scores.size() * 12);
    for (const auto& s : scores) {
        auto id = s.at("id").get<uint32_t>();
        auto distance = s.at("distance").get<double>();
        uint64_t bits;
        std::memcpy(&bits, &distance, 8);
        for (unsigned j = 0; j < 4; ++j)
            bytes.push_back(uint8_t(id >> (8 * j)));
        for (unsigned j = 0; j < 8; ++j)
            bytes.push_back(uint8_t(bits >> (8 * j)));
    }
    (void)operators::scores(bytes);
    return bytes;
}
std::set<uint32_t> state_set(const Value& value, const char* field, uint64_t count) {
    require(bool(value.state), "missing search state snapshot");
    const auto& data = value.state->data().at(field);
    require(data.is_array(), "search state set must be an array");
    std::set<uint32_t> out;
    for (const auto& x : data) {
        require(x.is_number_unsigned() && x.get<uint64_t>() < count, "invalid search state ID");
        require(out.insert(x.get<uint32_t>()).second, "duplicate search state ID");
    }
    return out;
}
} // namespace
Mapping::Mapping(Document doc,
                 Executor& ex,
                 runtime::Runtime& rt,
                 model::HostCpu& cpu,
                 const content::ContentStore& store,
                 const content::Layout& layout,
                 Read read,
                 std::vector<ObjectBinding> bindings,
                 model::Controller* controller,
                 Bind bind)
    : contract_(std::move(doc)), executor_(ex), runtime_(rt), cpu_(cpu), store_(store),
      layout_(layout), read_(std::move(read)), controller_(controller), bind_(std::move(bind)) {
    validate(contract_);
    require(contract_.operations.empty() && bool(read_),
            "mapping requires empty contract/read capability");
    require((contract_.manifest.mapping.at("placement") == "controller") == bool(controller_),
            "placement/backend mismatch");
    for (auto binding : bindings) {
        auto it =
            std::find_if(contract_.objects.begin(), contract_.objects.end(), [&](const auto& o) {
                return o.object_id == binding.object_id;
            });
        require(it != contract_.objects.end() && it->content_version == binding.version,
                "unknown object binding/version");
        require((it->object_type == "base_vectors" && binding.kind == content::Kind::Vector) ||
                    (it->object_type == "adjacency" && binding.kind == content::Kind::Adjacency),
                "object binding kind mismatch");
        require(objects_.emplace(binding.object_id, binding).second, "duplicate object binding");
    }
    for (const auto& o : contract_.objects)
        if (o.object_type != "query_vectors")
            require(objects_.count(o.object_id), "unbound logical object");
}
void Mapping::admit(uint64_t query,
                    std::vector<float> vector,
                    std::shared_ptr<const StateSnapshot> state) {
    require(query && state && state->identity().query == query && !queries_.count(query),
            "invalid/duplicate query admission");
    auto object =
        std::find_if(contract_.objects.begin(), contract_.objects.end(), [](const auto& o) {
            return o.object_type == "query_vectors";
        });
    require(object != contract_.objects.end() && vector.size() == object->width,
            "query dimension mismatch");
    (void)operators::fp32(vector); // finite input before any upload
    if (controller_) {
        auto opened = controller_->open(query, object->content_version, vector);
        if (session_bind)
            session_bind(query, "open", opened.id());
        runtime_.release(opened); // Controller owns the session's open dependency.
    }
    queries_.emplace(query, Query{std::move(vector), std::move(state), false, false, {}, {}});
}
std::vector<content::Selection> Mapping::selections(const Operation& op, const Values& in) const {
    const auto& access = op.access.at(0);
    const auto& binding = objects_.at(access.object_id);
    std::vector<content::Selection> out;
    std::set<uint32_t> unique;
    for (auto id : in.at("ids").data().get<std::vector<uint32_t>>()) {
        require(unique.insert(id).second, "duplicate logical read ID");
        content::ObjectRef object{binding.dataset, binding.kind, id, binding.version};
        auto size = store_.metadata(object).size;
        if (op.op == Operator::ScoreL2)
            require(size == 4 * op.params.at("dimensions").get<uint64_t>(),
                    "vector dimension mismatch");
        out.push_back({object, {0, size}});
    }
    // Preflight every logical record and extent before sending the first request.
    if (!out.empty())
        (void)layout_.resolve(out);
    return out;
}
std::vector<runtime::Handle>
Mapping::dispatch(const Operation& op, const Values& in, std::vector<runtime::Handle>& spawned) {
    const auto q = op.scope.query;
    struct Owned {
        runtime::Runtime& rt;
        std::vector<runtime::Handle> handles;
        ~Owned() {
            for (auto h : handles)
                rt.release(h);
        }
    } owned{runtime_, {}};
    auto child = [&](runtime::Handle h) {
        owned.handles.push_back(h);
        spawned.push_back(runtime_.retain(h));
        if (bind_)
            bind_(op.op_id, h.id());
        return h;
    };
    std::vector<runtime::Handle> tasks;
    if (op.op == Operator::ReadAdjacency || op.op == Operator::ScoreL2) {
        auto selected = selections(op, in);
        if (op.op == Operator::ReadAdjacency) {
            for (const auto& s : selected)
                tasks.push_back(child(read_(q, s)));
        } else {
            auto query = in.at("query").data().get<std::vector<float>>();
            require(query == queries_.at(q).vector, "SCORE query differs from admitted query");
            auto ids = in.at("ids").data().get<std::vector<uint32_t>>();
            if (controller_)
                tasks.push_back(child(controller_->score(q, selected)));
            else {
                const auto chunk = op.params.at("chunk").get<uint64_t>();
                for (size_t i = 0; i < selected.size(); i += chunk) {
                    const auto end = std::min<uint64_t>(selected.size(), i + chunk);
                    std::vector<runtime::Handle> reads;
                    for (size_t j = i; j < end; ++j)
                        reads.push_back(child(read_(q, selected[j])));
                    auto score =
                        child(cpu_.submit(q,
                                          reads,
                                          model::Kind::Distance,
                                          [query = operators::fp32(query),
                                           chunk_ids = std::vector<uint32_t>(
                                               ids.begin() + i, ids.begin() + end)](const auto& v) {
                                              return operators::squared_l2(query, v, chunk_ids);
                                          }));
                    tasks.push_back(score);
                }
            }
        }
    }
    for (auto& task : tasks)
        task = runtime_.retain(task);
    return tasks;
}
operators::Result Mapping::control(const Operation& op, const Values& in) const {
    const auto count = contract_.manifest.artifacts.at("dataset").at("shape").at(0).get<uint64_t>();
    if (op.op == Operator::MergeCandidates) {
        auto previous = packed(in.at("previous")), batch = packed(in.at("batch"));
        return search::core::merge_candidates(std::move(previous), batch, count);
    } else if (op.op == Operator::SelectFrontier) {
        auto all = packed(in.at("scores"));
        auto expanded = state_set(in.at("expanded"), "expanded", count);
        const auto& params = op.params;
        return search::core::select_frontier(all,
                                             expanded,
                                             params.at("L").get<uint64_t>(),
                                             params.at("B").get<uint64_t>(),
                                             params.at("E").get<uint64_t>());
    } else if (op.op == Operator::DedupNeighbors) {
        auto seen = state_set(in.at("scored"), "scored", count);
        const auto& adjacency = in.at("adjacency").data();
        std::vector<std::vector<uint32_t>> rows;
        // Preserve adapter validation before conversion to the pure value interface.
        for (const auto& row : adjacency) {
            require(row.at("degree").get<uint64_t>() == row.at("ids").size(),
                    "bad adjacency degree");
            for (const auto& id : row.at("ids"))
                require(id.get<uint64_t>() < count, "bad neighbor ID");
            rows.push_back(row.at("ids").get<std::vector<uint32_t>>());
        }
        return search::core::dedup_neighbors(rows, seen, count);
    }
    throw content::Error(content::Code::Unsupported, "unknown control operator");
}
Lease Mapping::submit(Operation op, std::vector<Lease> producers, States snapshots) {
    auto reject = [&](const char* detail) {
        Error error("InvalidArgument", "/mapping", detail, op.scope.query, op.op_id);
        error.time_ns = runtime_.now_ns();
        throw error;
    };
    auto it = queries_.find(op.scope.query);
    if (it == queries_.end())
        reject("query not admitted / already closed");
    auto& query = it->second;
    if (query.closing)
        reject("operation submitted after query close");
    if (op.op == Operator::QueryReady ? query.ready : !query.ready)
        reject("QUERY_READY admission/order mismatch");
    auto values = std::make_shared<Values>();
    Adapter adapter;
    adapter.launch = [this, op, values](auto parent, const Values& in) {
        if (op.op == Operator::QueryReady) {
            const auto& query = queries_.at(op.scope.query);
            *values = {{"query", {query.vector, {}}},
                       {"state", {nullptr, query.state}},
                       {"ready", {nullptr, {}}}};
            runtime_.start(parent);
            return;
        }
        std::vector<runtime::Handle> spawned, tasks;
        std::optional<runtime::Failure> launch_failure;
        try {
            tasks = dispatch(op, in, spawned);
        } catch (const content::Error& e) {
            launch_failure = runtime::Failure{e.code, op.scope.query, parent, 0, 0, 0, e.what()};
        } catch (const std::exception& e) {
            launch_failure = runtime::Failure{
                content::Code::BackendFailure, op.scope.query, parent, 0, 0, 0, e.what()};
        }
        if (launch_failure)
            tasks = std::move(spawned);
        else
            for (auto h : spawned)
                runtime_.release(h);
        struct Batch {
            size_t left;
            std::optional<runtime::Failure> failure;
            std::vector<Bytes> bytes;
        };
        auto batch = std::make_shared<Batch>(
            Batch{tasks.size(), launch_failure, std::vector<Bytes>(tasks.size())});
        auto finish = [this, op, values, parent, batch] {
            if (batch->failure) {
                auto fail = [this, op, failure = *batch->failure] {
                    executor_.fail_after_drain(op.op_id,
                                               failure.code,
                                               "child task=" + std::to_string(failure.task) + " " +
                                                   failure.detail);
                };
                if (controller_ && op.op == Operator::ScoreL2)
                    controller_->when_drained(op.scope.query, std::move(fail));
                else
                    fail();
                return;
            }
            if (op.op == Operator::ReadAdjacency) {
                Json rows = Json::array();
                for (const auto& b : batch->bytes) {
                    auto row = search::ids(b);
                    require(!row.empty() && uint64_t(row[0]) + 1 <= row.size(),
                            "bad adjacency record");
                    rows.push_back(
                        {{"degree", row[0]},
                         {"ids",
                          std::vector<uint32_t>(row.begin() + 1, row.begin() + 1 + row[0])}});
                }
                *values = {{"adjacency", {rows, {}}}};
            } else if (op.op == Operator::ScoreL2 || op.op == Operator::MergeCandidates) {
                Bytes bytes;
                for (const auto& b : batch->bytes)
                    bytes.insert(bytes.end(), b.begin(), b.end());
                *values = {{"scores", Value::scores(std::move(bytes))}};
            } else
                *values = {{"ids", {search::ids(batch->bytes.at(0)), {}}}};
            runtime_.start(parent);
        };
        if (tasks.empty()) {
            finish();
            return;
        }
        for (size_t i = 0; i < tasks.size(); ++i) {
            auto task = tasks[i];
            runtime_.when_all(
                {task}, op.op_id, [this, task, i, batch, finish, op_id = op.op_id](auto outcome) {
                    if (outcome.failure) {
                        if (!batch->failure)
                            batch->failure = outcome.failure;
                    } else
                        batch->bytes[i] = runtime_.try_get(task);
                    runtime_.release(task);
                    if (!--batch->left) {
                        try {
                            finish();
                        } catch (const content::Error& e) {
                            executor_.fail_after_drain(op_id, e.code, e.what());
                        } catch (const std::exception& e) {
                            executor_.fail_after_drain(
                                op_id, content::Code::InvalidArgument, e.what());
                        }
                    }
                });
        }
    };
    adapter.evaluate = [values](const Values&) {
        return std::move(*values);
    };
    adapter.service = [this](auto task) {
        runtime_.service_done(task);
    };
    if (op.op == Operator::MergeCandidates || op.op == Operator::SelectFrontier ||
        op.op == Operator::DedupNeighbors) {
        auto inputs = std::make_shared<Values>();
        auto host = cpu_.operation(
            op.scope.query, {}, model::Kind::Control, [this, op, inputs](const auto&) {
                return control(op, *inputs);
            });
        adapter.launch = [this, inputs, grant = std::move(host.grant), id = op.op_id](
                             auto task, const Values& in) {
            *inputs = in;
            if (bind_)
                bind_(id, task);
            grant(task);
        };
        adapter.evaluate = [evaluate = std::move(host.evaluate), op](const Values&) {
            auto bytes = evaluate({});
            if (op.op == Operator::MergeCandidates)
                return Values{{"scores", Value::scores(std::move(bytes))}};
            return Values{{"ids", {search::ids(bytes), {}}}};
        };
        adapter.service = std::move(host.service);
    }
    auto id = op.op_id, q = op.scope.query;
    auto lease =
        op.op == Operator::Join
            ? executor_.join(std::move(op), std::move(producers))
            : executor_.submit(
                  std::move(op), std::move(producers), std::move(adapter), std::move(snapshots));
    query.ready = true;
    query.active.emplace(id, executor_.retain(lease));
    executor_.when_all({lease}, id, [this, q, id](auto outcome) {
        auto& query = queries_.at(q);
        if (outcome.failure && !query.failure)
            query.failure = outcome.failure;
        auto& active = query.active;
        executor_.release(active.at(id));
        active.erase(id);
    });
    return lease;
}
runtime::Handle Mapping::close(uint64_t q) {
    auto& query = queries_.at(q);
    require(!query.closing, "duplicate query close");
    query.closing = true;
    std::vector<Lease> active;
    for (const auto& [id, lease] : query.active) {
        (void)id;
        active.push_back(executor_.retain(lease));
    }
    runtime::Operation op;
    op.query = q;
    op.grant = [this, q, active](auto parent) {
        // Wait each operation individually: when_all fails early on first failure.
        auto left = std::make_shared<size_t>(active.size());
        auto finish = [this, q, parent] {
            if (!controller_) {
                queries_.erase(q);
                runtime_.start(parent);
                return;
            }
            if (!controller_->has_session(q)) {
                auto failure = queries_.at(q).failure;
                queries_.erase(q);
                if (failure)
                    runtime_.fail_after_drain(parent, failure->code, failure->detail);
                else
                    runtime_.fail_after_drain(
                        parent, content::Code::InvalidHandle, "controller session absent at close");
                return;
            }
            auto closed = controller_->close(q);
            if (session_bind)
                session_bind(q, "close", closed.id());
            runtime_.when_all({closed}, q, [this, q, parent, closed](auto outcome) {
                runtime_.release(closed);
                queries_.erase(q);
                if (outcome.failure)
                    runtime_.fail_after_drain(
                        parent, outcome.failure->code, outcome.failure->detail);
                else
                    runtime_.start(parent);
            });
        };
        if (active.empty()) {
            finish();
            return;
        }
        for (auto lease : active)
            executor_.when_all({lease}, q, [this, lease, left, finish](auto) {
                executor_.release(lease);
                if (!--*left)
                    finish();
            });
    };
    op.evaluate = [](const auto&) {
        return Bytes{};
    };
    op.service = [this](auto parent) {
        runtime_.service_done(parent);
    };
    return runtime_.submit(std::move(op));
}
} // namespace ndpsim::ir
