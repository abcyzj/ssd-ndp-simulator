#include "search/replay.h"
#include "search/core.h"
#include "model/controller.h"
#include "search/native_observation.h"
#include <cstring>
#include <utility>

namespace ndpsim::search {
namespace {
constexpr size_t line_limit = 16 * 1024 * 1024;
void require(bool condition, const char* field) {
    if (!condition)
        throw std::runtime_error(std::string("replay mismatch: ") + field);
}
} // namespace

Replay::Replay(runtime::Runtime& rt,
               model::HostCpu& cpu,
               Config config,
               uint64_t query,
               uint32_t original,
               uint32_t dimension,
               std::ifstream& input,
               ir::Json index,
               std::function<Bytes()> load_query,
               Read read,
               GraphSearch::Read ssd_read,
               model::Controller* controller,
               bool legacy_cost,
               Observe observe,
               GraphSearch::Trace trace,
               GraphSearch::Done done,
               Fail fail,
               std::function<bool()> cancelled)
    : rt_(rt), cpu_(cpu), config_(config), query_(query), original_(original),
      dimension_(dimension), input_(input), index_(std::move(index)),
      load_query_(std::move(load_query)), read_(std::move(read)),
      ssd_read_(std::move(ssd_read)), controller_(controller), legacy_cost_(legacy_cost),
      observe_(std::move(observe)),
      trace_(std::move(trace)), done_(std::move(done)), fail_(std::move(fail)), cancelled_(std::move(cancelled)),
      position_(index_.at("offset").get<uint64_t>()),
      end_(model::checked_add(position_, index_.at("length").get<uint64_t>())),
      fresh_{config.entry} {
}
void Replay::guard(const std::function<void()>& action) {
    try {
        action();
    } catch (const std::exception& error) {
        if (auto pending = std::exchange(legacy_control_, {})) {
            rt_.fail(pending->gate.id(), content::Code::InvalidArgument, error.what());
            rt_.release(pending->gate);
            rt_.release(pending->service);
        }
        fail_("query " + std::to_string(original_) + " batch " +
              (row_.contains("batch") ? row_.at("batch").dump() : "?") + " op " +
              std::to_string(local_) + " " + op_ + ": " + error.what());
    }
}
void Replay::start() {
    guard([this] {
        advance();
    });
}
void Replay::emit(ir::Json event) {
    event["query"] = query_;
    event["original_query_id"] = original_;
    event["op"] = local_;
    event["operator"] = op_;
    event["time_ns"] = rt_.now_ns();
    observe_(event);
}
std::vector<uint32_t> Replay::score_ids(const Bytes& bytes) {
    std::vector<uint32_t> result;
    for (const auto& item : operators::scores(bytes))
        result.push_back(item.id);
    return result;
}
void Replay::verify(const Bytes& bytes,
                    const std::vector<uint32_t>& ids,
                    size_t count,
                    const Work& work) {
    const auto& expected = row_.at("expected");
    const auto& output = expected.at("output");
    require(expected.at("ids") == ids, "ordered IDs");
    require(output.at("count") == count && output.at("bytes") == bytes.size(), "output shape");
    auto hash = content::sha256(bytes);
    require(output.at("sha256") == hash, "output sha256");
    ir::Json actual = ir::Json::object();
    for (size_t i = 0; i < native::work_fields.size(); ++i)
        actual[native::work_names[i]] = work.*native::work_fields[i];
    require(actual == expected.at("work"), "Work");
    emit({{"type", "replay_verified"}, {"work", actual}, {"ids", ids}, {"sha256", hash}});
}
void Replay::state(const std::vector<uint32_t>& ids) {
    const auto& transition = row_.at("state");
    if (op_ == "INIT")
        require(transition.at("before").is_null(), "initial state");
    else {
        require(transition.at("before") == version_, "state before");
        ++version_;
    }
    require(transition.at("after") == version_, "state after");
    Bytes token;
    for (unsigned i = 0; i < 8; ++i)
        token.push_back(uint8_t(version_ >> (8 * i)));
    verify(token, ids, 1);
}
void Replay::access(const char* object, const std::vector<uint32_t>& ids, uint64_t bytes) {
    const auto& expected = row_.at("access");
    require(expected.at("object") == object && expected.at("ids") == ids &&
                expected.at("bytes") == bytes,
            "logical access");
}
void Replay::service(const char* stage,
                     model::CpuFeatures features,
                     std::function<operators::Result()> evaluate,
                     Next next) {
    service(stage, std::move(features), [evaluate = std::move(evaluate)](const auto&) {
        return evaluate();
    }, std::move(next), {});
}
void Replay::service(const char* stage,
                     model::CpuFeatures features,
                     model::HostCpu::Evaluate evaluate,
                     Next next,
                     std::vector<runtime::Handle> reads) {
    const bool l2 = std::string_view(stage) == "score_l2";
    auto actual = std::make_shared<Work>();
    const bool merge = std::string_view(stage) == "merge";
    const char* cpu_op = merge ? "MERGE_CANDIDATES" : op_.c_str();
    const char* model_stage = merge ? (op_ == "SCORE_L2" ? "merge_append" : "merge_finalize") : stage;
    std::optional<model::CpuStage> cost;
    if (!legacy_cost_)
        cost = model::CpuStage{cpu_op, model_stage,
                              [features = std::move(features)](const auto&, const auto&) {
                                  return features;
                              }};
    auto run = [this, stage, evaluate = std::move(evaluate), actual](const auto& inputs) {
        require(!cancelled_(), "batch stopped after failure");
        emit({{"type", "replay_stage"}, {"stage", stage}, {"task", task_}});
        auto result = evaluate(inputs);
        *actual = result.work;
        return result;
    };
    runtime::Handle handle;
    if (auto pending = legacy_control_) {
        require(reads.empty() && !l2, "legacy control gate owner");
        pending->evaluate = std::move(run);
        handle = pending->service;
        rt_.start(pending->gate.id());
        rt_.release(pending->gate);
        legacy_control_.reset();
    } else {
        handle = cpu_.submit(query_, reads, l2 ? model::Kind::Distance : model::Kind::Control,
                             std::move(run), std::move(cost));
        for (auto read : reads)
            rt_.release(read);
    }
    task_ = handle.id();
    rt_.when_all({handle}, query_, [this, handle, actual, next = std::move(next)](auto outcome) {
        guard([&] {
            if (cancelled_()) {
                rt_.release(handle);
                fail_("");
                return;
            }
            if (outcome.failure) {
                rt_.release(handle);
                throw std::runtime_error(outcome.failure->detail);
            }
            auto bytes = rt_.try_get(handle);
            rt_.release(handle);
            next(std::move(bytes), *actual);
        });
    });
}
void Replay::score_ready() {
    verify(batch_, fresh_, fresh_.size(), score_work_);
    if (controller_) {
        ir::Json scores = ir::Json::array();
        for (const auto& score : operators::scores(batch_))
            scores.push_back({{"id", score.id}, {"distance", score.distance}});
        emit({{"type", "score_visible"}, {"scores", std::move(scores)}});
        Bytes{}.swap(returned_scores_);
    }
    advance();
}
void Replay::score_chunk() {
    if (offset_ == fresh_.size()) {
        score_ready();
        return;
    }
    const auto end = std::min(fresh_.size(), offset_ + config_.chunk);
    group_.assign(fresh_.begin() + offset_, fresh_.begin() + end);
    offset_ = end;
    auto count = group_.size();
    if (controller_) {
        append_scores({}, count,
                      [this] { score_chunk(); });
        return;
    }
    vectors_.reserve(count);
    std::vector<runtime::Handle> reads;
    if (ssd_read_)
        for (auto id : group_)
            reads.push_back(ssd_read_(content::Kind::Vector, id));
    model::CpuFeatures features{{"vectors", count}, {"dimensions", dimension_}};
    service(
        "score_prepare",
        features,
        [this](const auto& inputs) {
            Work work;
            for (size_t i = 0; i < group_.size(); ++i) {
                auto id = group_[i];
                vectors_.push_back(ssd_read_ ? inputs.at(i) : read_(content::Kind::Vector, id, task_));
                work.read_bytes += vectors_.back().size();
                emit({{"type", "replay_read"},
                      {"object", "base"},
                      {"id", id},
                      {"bytes", vectors_.back().size()}});
                if (!ssd_read_)
                    require(scored_.insert(id).second, "duplicate score");
            }
            return operators::Result{{}, work};
        },
        [this, count, features](Bytes, Work) {
            service(
                "score_l2",
                features,
                [this] {
                    return operators::squared_l2(query_bytes_, vectors_, group_);
                },
                [this, count](Bytes packed, Work work) {
                    native::add_work(score_work_, work);
                    append_scores(std::move(packed), count, [this] { score_chunk(); });
                });
        },
        std::move(reads));
}
void Replay::append_scores(Bytes packed, size_t count, std::function<void()> next) {
    service(
        "merge",
        {{"old_scores", 0}, {"new_scores", count}, {"original_query_id", original_},
         {"sample_op", local_ + 1}, {"segment", (offset_ - count) / config_.chunk}},
        [this, count, packed = std::move(packed)] {
            if (controller_)
                batch_.insert(batch_.end(), returned_scores_.begin() + (offset_ - count) * 12,
                              returned_scores_.begin() + offset_ * 12);
            else
                batch_.insert(batch_.end(), packed.begin(), packed.end());
            decltype(vectors_){}.swap(vectors_);
            return operators::Result{};
        },
        [next = std::move(next)](Bytes, Work) { next(); });
}
void Replay::await(runtime::Handle handle, std::function<void(Bytes)> next) {
    await_all({handle}, [next = std::move(next)](auto values) {
        next(std::move(values.at(0)));
    });
}
void Replay::await_all(std::vector<runtime::Handle> handles,
                       std::function<void(std::vector<Bytes>)> next) {
    require(!handles.empty(), "empty service batch");
    struct Drain {
        std::vector<Bytes> values;
        size_t left;
        std::optional<runtime::Failure> failure;
    };
    auto drain = std::make_shared<Drain>(Drain{std::vector<Bytes>(handles.size()), handles.size(), {}});
    for (size_t i = 0; i < handles.size(); ++i) {
        auto handle = handles[i];
        rt_.when_all({handle}, query_, [this, handle, i, drain, next](auto outcome) {
            guard([&] {
                if (outcome.failure) {
                    if (!drain->failure)
                        drain->failure = outcome.failure;
                } else
                    drain->values[i] = rt_.try_get(handle);
                rt_.release(handle);
                if (--drain->left)
                    return;
                if (cancelled_()) {
                    fail_("");
                    return;
                }
                if (drain->failure)
                    throw std::runtime_error(drain->failure->detail);
                next(std::move(drain->values));
            });
        });
    }
}
void Replay::reserve_legacy_control() {
    if (legacy_control_)
        return;
    // Preserve the legacy submission timestamp. The gate has no service
    // cost and can open only after the preceding static value/Work check.
    runtime::Operation gate;
    gate.query = query_;
    gate.grant = [](auto) {};
    gate.evaluate = [](const auto&) { return Bytes{}; };
    gate.service = [this](auto id) { rt_.service_done(id); };
    auto pending = std::make_shared<LegacyControl>();
    pending->gate = rt_.submit(std::move(gate));
    pending->service = cpu_.submit(query_, {pending->gate}, model::Kind::Control,
                                   [pending](const auto&) { return pending->evaluate({}); });
    legacy_control_ = std::move(pending);
}
void Replay::legacy_host_score() {
    std::vector<runtime::Handle> scores;
    for (size_t offset = 0; offset < fresh_.size(); offset += config_.chunk) {
        const auto end = std::min(fresh_.size(), offset + config_.chunk);
        std::vector<uint32_t> ids(fresh_.begin() + offset, fresh_.begin() + end);
        std::vector<runtime::Handle> reads;
        for (auto id : ids)
            reads.push_back(ssd_read_(content::Kind::Vector, id));
        scores.push_back(cpu_.submit(
            query_, reads, model::Kind::Distance,
            [this, ids = std::move(ids)](const auto& values) {
                auto result = operators::squared_l2(query_bytes_, values, ids);
                native::add_work(score_work_, result.work);
                return result;
            }));
        for (auto read : reads)
            rt_.release(read);
    }
    reserve_legacy_control();
    await_all(std::move(scores), [this](auto chunks) {
        for (const auto& packed : chunks)
            batch_.insert(batch_.end(), packed.begin(), packed.end());
        for (auto id : fresh_)
            emit({{"type", "replay_read"}, {"object", "base"}, {"id", id},
                  {"bytes", uint64_t(dimension_) * 4}});
        score_ready();
    });
}
void Replay::controller_score() {
    std::vector<content::Selection> selections;
    for (auto id : fresh_)
        selections.push_back({{1, content::Kind::Vector, id, 1}, {0, uint64_t(dimension_) * 4}});
    auto physical = std::make_shared<Work>();
    auto chunks = std::make_shared<size_t>(0);
    auto score = controller_->score(query_, selections, [physical, chunks](const Work& work) {
        native::add_work(*physical, work);
        ++*chunks;
    });
    if (legacy_cost_)
        reserve_legacy_control();
    await(score, [this, physical, chunks](Bytes bytes) {
        const auto semantic_chunks = 1 + (fresh_.size() - 1) / config_.chunk;
        const auto physical_chunks = 1 + (fresh_.size() - 1) / controller_->chunk_size();
        require(*chunks == physical_chunks, "controller physical chunk coverage");
        score_work_ = *physical;
        const auto extra_query_reads = uint64_t(dimension_) * 4 * (physical_chunks - semantic_chunks);
        require(score_work_.read_bytes >= extra_query_reads, "controller query read bytes");
        score_work_.read_bytes -= extra_query_reads;
        emit({{"type", "replay_controller_work"}, {"chunks", *chunks},
              {"physical_read_bytes", physical->read_bytes}, {"flops", physical->flops}});
        require(bytes.size() == fresh_.size() * 12, "controller score bytes");
        for (auto id : fresh_)
            emit({{"type", "replay_read"}, {"object", "base"}, {"id", id},
                  {"bytes", uint64_t(dimension_) * 4}});
        if (legacy_cost_) {
            batch_ = std::move(bytes);
            score_ready();
        } else {
            returned_scores_ = std::move(bytes);
            score_chunk();
        }
    });
}
void Replay::mark(const std::vector<uint32_t>& values, std::set<uint32_t>& target) {
    if (!ssd_read_) {
        state(values);
        advance();
        return;
    }
    auto insert = [&values, &target] {
        for (auto id : values)
            require(target.insert(id).second, "duplicate state insertion");
        return operators::Result{};
    };
    if (legacy_cost_) {
        insert();
        state(values);
        advance();
    } else
        service("state", {{"objects", values.size()}}, insert, [this, &values](Bytes, Work) {
            state(values);
            advance();
        });
}
void Replay::advance() {
    require(position_ < end_ && local_ < index_.at("operations").get<uint64_t>(), "shard ended");
    // Other active sessions can read the shared stream between continuations.
    input_.seekg(position_);
    require(bool(input_), "cannot seek to operation");
    std::string line;
    for (;;) {
        require(position_ < end_ && line.size() < line_limit, "operation line bound");
        const auto ch = input_.get();
        require(ch != EOF, "truncated operation");
        ++position_;
        if (ch == '\n')
            break;
        line.push_back(static_cast<char>(ch));
    }
    row_ = ir::Json::parse(line);
    ++local_;
    require(row_.at("op") == local_ && row_.at("query") == original_, "operation scope");
    op_ = row_.at("operator").get<std::string>();
    emit({{"type", "replay_submit"}});
    if (op_ == "INIT") {
        const auto bytes = uint64_t(dimension_) * 4;
        access("queries", {original_}, bytes);
        auto prepare = [this] {
            query_bytes_ = load_query_();
            operators::squared_l2(query_bytes_, {}, {});
            emit({{"type", "replay_read"}, {"object", "queries"}, {"id", original_},
                  {"bytes", query_bytes_.size()}});
            return operators::Result{};
        };
        if (legacy_cost_) {
            prepare();
            init_query();
        } else
            service("init", {{"copied_bytes", bytes}}, prepare, [this](Bytes, Work) { init_query(); });
    } else if (op_ == "MARK_SCORED") {
        mark(fresh_, scored_);
    } else if (op_ == "SCORE_L2") {
        access("base", fresh_, uint64_t(dimension_) * 4 * fresh_.size());
        score_work_ = {};
        offset_ = 0;
        if (controller_)
            controller_score();
        else if (legacy_cost_)
            legacy_host_score();
        else
            score_chunk();
    } else if (op_ == "MERGE_CANDIDATES") {
        service(
            "merge",
            {{"old_scores", rank_.size() / 12}, {"new_scores", batch_.size() / 12}},
            [this] {
                return core::merge_candidates(std::move(rank_), batch_, config_.count);
            },
            [this](Bytes bytes, Work work) {
                verify(bytes, score_ids(bytes), bytes.size() / 12, work);
                rank_ = std::move(bytes);
                Bytes{}.swap(batch_);
                advance();
            });
    } else if (op_ == "SELECT_FRONTIER") {
        service(
            "select",
            {{"old_scores", rank_.size() / 12},
             {"expanded", expanded_.size()},
             {"L", config_.width},
             {"B", config_.batch},
             {"E", config_.budget}},
            [this] {
                return core::select_frontier(
                    rank_, expanded_, config_.width, config_.batch, config_.budget);
            },
            [this](Bytes bytes, Work work) {
                chosen_ = ids(bytes);
                verify(bytes, chosen_, chosen_.size(), work);
                auto ranked = operators::scores(rank_);
                if (ssd_read_ && ranked.size() > config_.width)
                    ranked.resize(config_.width);
                trace_({rt_.now_ns(), std::move(ranked), chosen_});
                advance();
            });
    } else if (op_ == "MARK_EXPANDED") {
        adjacency_ = 0;
        mark(chosen_, expanded_);
    } else if (op_ == "READ_ADJACENCY") {
        auto id = chosen_.at(adjacency_++);
        const auto& expected = row_.at("access");
        require(expected.at("object") == "graph" &&
                    expected.at("ids") == std::vector<uint32_t>{id},
                "adjacency address");
        auto prepare = [this, id](const Bytes& payload) {
            auto row = ids(payload);
            require(!row.empty() && row.front() == row.size() - 1, "adjacency encoding");
            access("graph", {id}, payload.size());
            if (!ssd_read_)
                require(expanded_.insert(id).second, "duplicate expansion");
            neighbors_.emplace_back(row.begin() + 1, row.end());
            emit({{"type", "replay_read"}, {"object", "graph"}, {"id", id},
                  {"bytes", payload.size()}});
            return row.front();
        };
        auto complete = [this, id](const Bytes& payload, uint64_t degree) {
            verify(payload, {id}, degree);
            joined_.insert(joined_.end(), payload.begin(), payload.end());
            advance();
        };
        if (legacy_cost_) {
            if (adjacency_ == 1) {
                std::vector<runtime::Handle> reads;
                for (auto parent : chosen_)
                    reads.push_back(ssd_read_(content::Kind::Adjacency, parent));
                reserve_legacy_control();
                await_all(std::move(reads), [this, prepare, complete](auto values) {
                    returned_adjacency_ = std::move(values);
                    auto payload = std::move(returned_adjacency_.front());
                    complete(payload, prepare(payload));
                });
            } else {
                auto payload = std::move(returned_adjacency_.at(adjacency_ - 1));
                complete(payload, prepare(payload));
            }
            return;
        }
        // Prepare must derive features from the actual row, not the package's expected degree.
        auto payload = std::make_shared<Bytes>();
        auto degree = std::make_shared<uint64_t>();
        std::vector<runtime::Handle> reads;
        if (ssd_read_)
            reads.push_back(ssd_read_(content::Kind::Adjacency, id));
        auto handle = cpu_.submit(
            query_,
            reads,
            model::Kind::Control,
            [this, id, payload, degree, prepare](const auto& inputs) {
                require(!cancelled_(), "batch stopped after failure");
                emit({{"type", "replay_stage"}, {"stage", "adjacency_prepare"}, {"task", task_}});
                *payload = ssd_read_ ? inputs.at(0) : read_(content::Kind::Adjacency, id, task_);
                *degree = prepare(*payload);
                Work work;
                work.read_bytes = payload->size();
                return operators::Result{{}, work};
            },
            model::CpuStage{op_, "adjacency_prepare", [degree](const auto&, const auto&) {
                                return model::CpuFeatures{{"edges", *degree}};
                            }});
        for (auto read : reads)
            rt_.release(read);
        task_ = handle.id();
        await(handle, [payload, degree, complete](Bytes) { complete(*payload, *degree); });
    } else if (op_ == "JOIN") {
        verify(joined_, chosen_, neighbors_.size());
        Bytes{}.swap(joined_);
        returned_adjacency_.clear();
        advance();
    } else if (op_ == "DEDUP_NEIGHBORS") {
        uint64_t edges = 0;
        for (const auto& row : neighbors_)
            edges += row.size();
        service(
            "dedup",
            {{"edges", edges}, {"scored", scored_.size()}},
            [this] {
                return core::dedup_neighbors(neighbors_, scored_, config_.count);
            },
            [this](Bytes bytes, Work work) {
                fresh_ = ids(bytes);
                verify(bytes, fresh_, fresh_.size(), work);
                decltype(neighbors_){}.swap(neighbors_);
                advance();
            });
    } else if (op_ == "FINISH") {
        require(chosen_.empty() && position_ == end_ && local_ == index_.at("operations"),
                "terminal position");
        if (legacy_cost_) {
            rank_.resize(std::min<size_t>(config_.k, rank_.size() / 12) * 12);
            finish();
            return;
        }
        auto top = std::make_shared<Bytes>();
        auto count = std::min<size_t>(config_.k, rank_.size() / 12);
        service(
            "finish",
            {{"objects", count}},
            [this, top, count] {
                top->assign(rank_.begin(), rank_.begin() + count * 12);
                return operators::Result{};
            },
            [this, top](Bytes, Work) {
                rank_ = std::move(*top);
                finish();
            });
    } else
        throw std::runtime_error("unsupported replay operator");
}
void Replay::init_query() {
    state({});
    if (!controller_) {
        advance();
        return;
    }
    std::vector<float> values;
    for (auto bits : ids(query_bytes_)) {
        float value;
        std::memcpy(&value, &bits, sizeof(value));
        values.push_back(value);
    }
    if (legacy_cost_)
        reserve_legacy_control();
    await(controller_->open(query_, 1, values), [this](Bytes) { advance(); });
}
void Replay::finish() {
    verify(rank_, score_ids(rank_), rank_.size() / 12);
    require(index_.at("terminal") ==
                (expanded_.size() == config_.budget ? "budget" : "exhausted"),
            "terminal reason");
    done_(operators::scores(rank_), expanded_.size());
}
} // namespace ndpsim::search
