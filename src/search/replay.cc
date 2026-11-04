#include "search/replay.h"
#include "search/core.h"
#include "model/controller.h"
#include "search/native_observation.h"

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
               Observe observe,
               GraphSearch::Trace trace,
               GraphSearch::Done done,
               Fail fail,
               std::function<bool()> cancelled)
    : rt_(rt), cpu_(cpu), config_(config), query_(query), original_(original),
      dimension_(dimension), input_(input), index_(std::move(index)),
      load_query_(std::move(load_query)), read_(std::move(read)), observe_(std::move(observe)),
      trace_(std::move(trace)), done_(std::move(done)), fail_(std::move(fail)), cancelled_(std::move(cancelled)),
      position_(index_.at("offset").get<uint64_t>()),
      end_(model::checked_add(position_, index_.at("length").get<uint64_t>())),
      fresh_{config.entry} {
}
void Replay::guard(const std::function<void()>& action) {
    try {
        action();
    } catch (const std::exception& error) {
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
    const bool l2 = std::string_view(stage) == "score_l2";
    auto actual = std::make_shared<Work>();
    const bool merge = std::string_view(stage) == "merge";
    const char* cpu_op = merge ? "MERGE_CANDIDATES" : op_.c_str();
    const char* model_stage = merge ? (op_ == "SCORE_L2" ? "merge_append" : "merge_finalize") : stage;
    auto handle = cpu_.submit(
        query_,
        {},
        l2 ? model::Kind::Distance : model::Kind::Control,
        [this, stage, evaluate = std::move(evaluate), actual](const auto&) {
            require(!cancelled_(), "batch stopped after failure");
            emit({{"type", "replay_stage"}, {"stage", stage}, {"task", task_}});
            auto result = evaluate();
            *actual = result.work;
            return result;
        },
        model::CpuStage{cpu_op, model_stage, [features = std::move(features)](const auto&, const auto&) {
                            return features;
                        }});
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
void Replay::score_chunk() {
    if (offset_ == fresh_.size()) {
        verify(batch_, fresh_, fresh_.size(), score_work_);
        advance();
        return;
    }
    const auto end = std::min(fresh_.size(), offset_ + config_.chunk);
    group_.assign(fresh_.begin() + offset_, fresh_.begin() + end);
    offset_ = end;
    auto count = group_.size();
    vectors_.reserve(count);
    model::CpuFeatures features{{"vectors", count}, {"dimensions", dimension_}};
    service(
        "score_prepare",
        features,
        [this] {
            Work work;
            for (auto id : group_) {
                vectors_.push_back(read_(content::Kind::Vector, id, task_));
                work.read_bytes += vectors_.back().size();
                emit({{"type", "replay_read"},
                      {"object", "base"},
                      {"id", id},
                      {"bytes", vectors_.back().size()}});
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
                    service(
                        "merge",
                        {{"old_scores", 0}, {"new_scores", count}},
                        [this, packed = std::move(packed)] {
                            batch_.insert(batch_.end(), packed.begin(), packed.end());
                            decltype(vectors_){}.swap(vectors_);
                            return operators::Result{};
                        },
                        [this](Bytes, Work) {
                            score_chunk();
                        });
                });
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
        service(
            "init",
            {{"copied_bytes", bytes}},
            [this] {
                query_bytes_ = load_query_();
                operators::squared_l2(query_bytes_, {}, {});
                emit({{"type", "replay_read"},
                      {"object", "queries"},
                      {"id", original_},
                      {"bytes", query_bytes_.size()}});
                return operators::Result{};
            },
            [this](Bytes, Work) {
                state({});
                advance();
            });
    } else if (op_ == "MARK_SCORED") {
        state(fresh_);
        advance();
    } else if (op_ == "SCORE_L2") {
        access("base", fresh_, uint64_t(dimension_) * 4 * fresh_.size());
        score_work_ = {};
        offset_ = 0;
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
                trace_({rt_.now_ns(), operators::scores(rank_), chosen_});
                advance();
            });
    } else if (op_ == "MARK_EXPANDED") {
        state(chosen_);
        adjacency_ = 0;
        advance();
    } else if (op_ == "READ_ADJACENCY") {
        auto id = chosen_.at(adjacency_++);
        auto payload = std::make_shared<Bytes>();
        // Prepare must derive features from the actual row, not the package's expected degree.
        auto degree = std::make_shared<uint64_t>();
        auto handle = cpu_.submit(
            query_,
            {},
            model::Kind::Control,
            [this, id, payload, degree](const auto&) {
                const auto& expected = row_.at("access");
                require(expected.at("object") == "graph" &&
                            expected.at("ids") == std::vector<uint32_t>{id},
                        "adjacency address");
                require(!cancelled_(), "batch stopped after failure");
            emit({{"type", "replay_stage"}, {"stage", "adjacency_prepare"}, {"task", task_}});
                *payload = read_(content::Kind::Adjacency, id, task_);
                auto row = ids(*payload);
                require(!row.empty() && row.front() == row.size() - 1, "adjacency encoding");
                *degree = row.front();
                access("graph", {id}, payload->size());
                require(expanded_.insert(id).second, "duplicate expansion");
                neighbors_.emplace_back(row.begin() + 1, row.end());
                emit({{"type", "replay_read"},
                      {"object", "graph"},
                      {"id", id},
                      {"bytes", payload->size()}});
                Work work;
                work.read_bytes = payload->size();
                return operators::Result{{}, work};
            },
            model::CpuStage{op_, "adjacency_prepare", [degree](const auto&, const auto&) {
                                return model::CpuFeatures{{"edges", *degree}};
                            }});
        task_ = handle.id();
        rt_.when_all({handle}, query_, [this, id, handle, payload, degree](auto outcome) {
            guard([&] {
                rt_.release(handle);
                if (outcome.failure)
                    throw std::runtime_error(outcome.failure->detail);
                verify(*payload, {id}, *degree);
                joined_.insert(joined_.end(), payload->begin(), payload->end());
                advance();
            });
        });
    } else if (op_ == "JOIN") {
        verify(joined_, chosen_, neighbors_.size());
        Bytes{}.swap(joined_);
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
        auto top = std::make_shared<Bytes>();
        auto count = std::min<size_t>(config_.k, rank_.size() / 12);
        service(
            "finish",
            {{"objects", count}},
            [this, top, count] {
                top->assign(rank_.begin(), rank_.begin() + count * 12);
                return operators::Result{};
            },
            [this, top, count](Bytes, Work) {
                verify(*top, score_ids(*top), count);
                require(index_.at("terminal") ==
                            (expanded_.size() == config_.budget ? "budget" : "exhausted"),
                        "terminal reason");
                done_(operators::scores(*top), expanded_.size());
            });
    } else
        throw std::runtime_error("unsupported replay operator");
}
} // namespace ndpsim::search
