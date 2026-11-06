#include "timing/controller.h"
namespace ndpsim::timing {
namespace {
void require(bool ok, const char* why) {
    if (!ok)
        throw std::invalid_argument(why);
}
uint64_t ceil_div(uint64_t a, uint64_t b) {
    return a / b + (a % b != 0);
}
} // namespace
MetadataController::MetadataController(Runtime& r,
                                       MetadataLocalReads& reads,
                                       MetadataPcie& p,
                                       const MessageRules& rules,
                                       EventBridge& b,
                                       EventBridge::Clock n,
                                       ControllerResources c)
    : runtime_(r), reads_(reads), transport_(p), rules_(rules), now_(n), config_(c),
      buffer_(b, n, c.capacity_bytes), port_(b, n, 1), compute_(b, n, c.compute_slots),
      endpoint_(b, n, 1) {
    require(c.query_bound && c.block_bound && c.admission && c.staging_bytes_per_ns &&
                c.compute_slots,
            "invalid controller resources");
    require(c.block_bound <= c.capacity_bytes &&
                c.admission <= (c.capacity_bytes - c.block_bound) / c.query_bound,
            "capacity cannot retain admitted queries plus one maximum block");
}
uint64_t MetadataController::task() {
    next_task_ = checked_add(next_task_, 1);
    return next_task_;
}
void MetadataController::send(Message m, std::function<void()> done) {
    transport_.send(m, [this, done = std::move(done)](Message arrived) {
        conversation_.arrive(arrived);
        done();
    });
}
void MetadataController::stage(std::string key, uint64_t bytes, std::function<void()> done) {
    accesses_.push_back({key, bytes, now_(), ceil_div(bytes, config_.staging_bytes_per_ns)});
    port_.submit(
        std::move(key),
        ceil_div(bytes, config_.staging_bytes_per_ns),
        [] {},
        [done = std::move(done)](bool completed) {
            if (!completed)
                throw std::logic_error("port cancelled");
            done();
        });
}
CompletionToken MetadataController::open(Message m) {
    require(m.kind == MessageKind::Open && !sessions_.count(m.query) &&
                !retired_.contains(m.query) && sessions_.size() < config_.admission,
            "invalid open/admission full");
    m.task = task();
    auto size = rules_.size(m);
    require(size.logical <= config_.query_bound, "query exceeds resource bound");
    auto s = std::make_shared<Session>();
    s->open = m;
    s->query_bytes = size.logical;
    auto ack = m;
    ack.kind = MessageKind::OpenAck;
    ack.count = ack.dimension = ack.chunk = 0;
    ack.token = checked_add(next_token_, 1);
    rules_.size(ack);
    next_token_ = ack.token;
    s->open.token = ack.token;
    Operation o;
    o.key = {m.query, "open-" + std::to_string(m.task), "controller"};
    o.resource = &endpoint_;
    o.input = [this, s, m, ack](Done done) {
        buffer_.reserve(s->query_bytes, "query", [this, s, m, ack, done](uint64_t credit) {
            s->credit = credit;
            send(m, [this, s, ack, done] {
                stage("query-write:" + std::to_string(ack.query) + ":" + std::to_string(ack.task),
                      s->query_bytes,
                      [this, ack, done] {
                          send(ack, [done] {
                              done({});
                          });
                      });
            });
        });
    };
    auto t = runtime_.submit(std::move(o));
    s->token = runtime_.retain(t);
    sessions_.emplace(m.query, s);
    runtime_.when_complete(t, [s](Outcome x) {
        s->failed = bool(x.failure);
    });
    return t;
}
void MetadataController::boundary(const std::shared_ptr<Job>& j,
                                  const std::string& name,
                                  std::function<void()> next) {
    if (observe)
        observe(name, j->token.id());
    if (!j->error.empty()) {
        reply(j);
        return;
    }
    next();
}
void MetadataController::release_input(const std::shared_ptr<Job>& j) {
    if (j->credit && !j->input_released) {
        buffer_.release_part(j->credit,
                             checked_add(j->request.reads.sector_bytes, j->request.scratch_bytes));
        j->input_released = true;
    }
}
void MetadataController::reply(const std::shared_ptr<Job>& j) {
    release_input(j); // Called only after all issued input/port/compute service has drained.
    auto m = j->request.result;
    if (!j->error.empty()) {
        m = j->request.command;
        m.kind = MessageKind::Error;
        m.count = m.dimension = m.chunk = 0;
        m.status = 5;
    }
    if (!j->sent) {
        if (j->credit) {
            buffer_.release(j->credit);
            j->credit = 0;
        }
        j->done({Failure{j->token.id(), "ControllerFailure", j->error}});
        return;
    }
    send(m, [this, j] {
        if (j->credit) {
            buffer_.release(j->credit);
            j->credit = 0;
        }
        j->replied = true;
        if (j->error.empty())
            j->done({});
        else
            j->done({Failure{j->token.id(), "ControllerFailure", j->error}});
    });
}
CompletionToken MetadataController::execute_profile(ControllerRequest request,
                                                    const cost::Profile& profile,
                                                    const cost::Input& input) {
    const auto& o = request.operation;
    require(input.key.query == o.key.query && input.key.op == o.key.op &&
                input.key.stage == o.key.stage && input.work == o.work &&
                input.features == o.features && input.location == "controller",
            "Controller Profile stage/Work/features/location mismatch");
    request.operation.duration_ns =
        profile.demand(input); // Includes applicability/target/binding checks.
    request.operation.service_key = "profile:" + profile.source_sha256() + ":" + input.operator_id +
                                    ":" + input.operator_version + ":" + input.stage_kind + ":" +
                                    input.context + ":" + std::to_string(input.key.query) + ":" +
                                    input.key.op + ":" + input.key.stage;
    return execute(std::move(request));
}
CompletionToken MetadataController::execute(ControllerRequest request) {
    auto it = sessions_.find(request.command.query);
    require(it != sessions_.end() && !it->second->closing && !it->second->failed,
            "execute without live session");
    auto s = it->second;
    require(request.command.kind == MessageKind::Execute &&
                request.result.kind == MessageKind::Result,
            "execute/result kinds required");
    request.command.task = task();
    request.command.token = s->open.token;
    auto& c = request.command;
    auto& r = request.result;
    r.task = c.task;
    r.token = c.token;
    require(c.version == s->open.version && c.protocol == s->open.protocol && r.query == c.query &&
                r.version == c.version && r.protocol == c.protocol && r.chunk == c.chunk &&
                r.count == c.count && c.dimension == s->open.dimension && !s->chunks.count(c.chunk),
            "request/result/session metadata mismatch");
    auto command_size = rules_.size(c);
    auto result_size = rules_.size(r);
    require(request.scratch_bytes >= command_size.payload, "command scratch smaller than envelope");
    reads_.validate(request.reads);
    auto bytes = checked_add(checked_add(request.reads.sector_bytes, result_size.logical),
                             request.scratch_bytes);
    require(bytes <= config_.block_bound, "block exceeds declared capacity bound");
    auto& o = request.operation;
    require(o.key.query == c.query && !o.key.op.empty() && !o.key.stage.empty() &&
                !o.service_key.empty() && !o.input && !o.send_return && !o.resource,
            "explicit controller stage/demand required");
    uint64_t output = 0;
    for (const auto& v : o.outputs)
        output = checked_add(output, v.logical_bytes);
    require(output == result_size.logical, "output description/message size mismatch");
    checked_add(now_(), o.duration_ns);
    auto j = std::make_shared<Job>();
    j->request = std::move(request);
    j->session = s;
    Operation outer = j->request.operation;
    outer.resource = &endpoint_;
    outer.duration_ns = 0;
    outer.inputs.push_back(s->token);
    outer.input = [this, j, bytes](Done done) {
        j->done = std::move(done);
        j->ticket = buffer_.reserve(bytes, "block", [this, j](uint64_t credit) {
            j->credit = credit;
            boundary(j, "reserved", [this, j] {
                begin_job(j);
            });
        });
    };
    auto t = runtime_.submit(std::move(outer));
    j->token = runtime_.retain(t);
    s->chunks.insert(j->request.command.chunk);
    ++s->outstanding;
    jobs_.emplace(t.id(), j);
    runtime_.when_complete(t, [this, j](Outcome) {
        if (j->credit)
            buffer_.release(j->credit);
        --j->session->outstanding;
        jobs_.erase(j->token.id());
        runtime_.release(j->token);
        if (!j->session->outstanding && j->session->drained) {
            auto done = std::move(j->session->drained);
            done();
        }
    });
    return t;
}
void MetadataController::stage_job(const std::shared_ptr<Job>& j,
                                   std::string name,
                                   uint64_t bytes,
                                   std::function<void()> done) {
    const auto& k = j->request.operation.key;
    stage(
        std::to_string(k.query) + ":" + k.op + ":" + k.stage + ":" + name, bytes, std::move(done));
}
void MetadataController::begin_job(const std::shared_ptr<Job>& j) {
    send(j->request.command, [this, j] {
        j->sent = true;
        boundary(j, "command", [this, j] {
            reads_.submit(j->request.reads, j->request.operation.key, [this, j](auto) {
                boundary(j, "local-read", [this, j] {
                    stage_job(j, "input-write", j->request.reads.sector_bytes, [this, j] {
                        input_ready(j);
                    });
                });
            });
        });
    });
}
void MetadataController::input_ready(const std::shared_ptr<Job>& j) {
    boundary(j, "staged", [this, j] {
        auto bytes = checked_add(j->request.reads.sector_bytes, j->session->query_bytes);
        bytes = checked_add(bytes, j->request.scratch_access_bytes);
        stage_job(j, "compute-read", bytes, [this, j] {
            compute_ready(j);
        });
    });
}
void MetadataController::compute_ready(const std::shared_ptr<Job>& j) {
    boundary(j, "compute-ready", [this, j] {
        compute_.submit(
            j->request.operation.service_key,
            j->request.operation.duration_ns,
            [] {},
            [this, j](bool completed) {
                if (!completed)
                    throw std::logic_error("compute cancelled");
                release_input(j);
                computed(j);
            });
    });
}
void MetadataController::computed(const std::shared_ptr<Job>& j) {
    boundary(j, "computed", [this, j] {
        stage_job(j, "result-write", rules_.size(j->request.result).logical, [this, j] {
            result_ready(j);
        });
    });
}
void MetadataController::result_ready(const std::shared_ptr<Job>& j) {
    boundary(j, "result", [this, j] {
        stage_job(j, "result-read", rules_.size(j->request.result).logical, [this, j] {
            reply(j);
        });
    });
}
void MetadataController::fail(CompletionToken t, std::string detail) {
    runtime_.state(t); // Reject foreign/released tokens before matching the numeric ID.
    auto it = jobs_.find(t.id());
    require(it != jobs_.end() && !detail.empty(), "unknown job/empty failure");
    auto j = it->second;
    require(!j->replied, "job return already drained");
    j->session->failed = true;
    // Stop unissued work for this failed session, while each accepted service
    // retains its credit until its own actual callback/response drains.
    for (const auto& entry : jobs_) {
        const auto& active = entry.second;
        if (active->session != j->session || active->replied || !active->error.empty())
            continue;
        active->error = detail;
        if (active->ticket && !active->credit && buffer_.cancel(active->ticket))
            reply(active);
    }
}
CompletionToken MetadataController::close(uint64_t query) {
    auto it = sessions_.find(query);
    require(it != sessions_.end() && !it->second->closing, "invalid close");
    auto s = it->second;
    auto m = s->open;
    m.kind = MessageKind::Close;
    m.task = task();
    m.count = m.dimension = m.chunk = 0;
    rules_.size(m);
    auto ack = m;
    ack.kind = MessageKind::CloseAck;
    Operation o;
    o.key = {query, "close-" + std::to_string(m.task), "controller"};
    o.resource = &endpoint_;
    o.inputs = {s->token};
    o.input = [this, s, m, ack](Done done) {
        auto drain = [this, s, m, ack, done] {
            send(m, [this, s, ack, done] {
                send(ack, [this, s, done] {
                    buffer_.release(s->credit);
                    runtime_.release(s->token);
                    retired_.insert(s->open.query);
                    sessions_.erase(s->open.query);
                    done({});
                });
            });
        };
        if (s->outstanding)
            s->drained = std::move(drain);
        else
            drain();
    };
    auto t = runtime_.submit(std::move(o));
    s->closing = true;
    return t;
}
} // namespace ndpsim::timing
