#include "model/controller.h"
#include <algorithm>
#include <set>
#include <sstream>
namespace ndpsim::model {
using content::Code;
using content::Error;
using transport::Kind;
uint64_t checked_add(uint64_t a, uint64_t b) {
    if (b > UINT64_MAX - a)
        throw Error(Code::Overflow, "controller addition");
    return a + b;
}
uint64_t checked_mul(uint64_t a, uint64_t b) {
    if (a && b > UINT64_MAX / a)
        throw Error(Code::Overflow, "controller multiplication");
    return a * b;
}
namespace {
uint64_t ceil_div(uint64_t a, uint64_t b) {
    return a / b + (a % b != 0);
}
} // namespace
FifoResource::FifoResource(EventBridge& b, EventBridge::Clock n, uint64_t s)
    : bridge_(b), now_(std::move(n)), slots_(s) {
    if (!s)
        throw Error(Code::InvalidArgument, "zero resource slots");
}
void FifoResource::submit(content::Context c,
                          std::string stage,
                          uint64_t bytes,
                          uint64_t d,
                          std::function<void()> begin,
                          std::function<void()> done) {
    checked_add(now_(), d);
    queue_.push_back({c, std::move(stage), bytes, d, now_(), std::move(begin), std::move(done)});
    pump();
}
void FifoResource::pump() {
    while (busy_ < slots_ && !queue_.empty()) {
        auto j = std::move(queue_.front());
        queue_.pop_front();
        ++busy_;
        auto end = checked_add(now_(), j.duration);
        records_.push_back(
            {j.context.query, j.context.task, j.submitted, now_(), end, j.bytes, j.stage});
        // Begin is also deferred: zero duration never invokes consumers inline.
        bridge_.enqueue(now_(),
                        EventBridge::ResourceCompleteRelease,
                        j.stage + " begin",
                        [begin = std::move(j.begin)] {
                            if (begin)
                                begin();
                        });
        bridge_.enqueue(end,
                        EventBridge::ResourceCompleteRelease,
                        j.stage + " done",
                        [this, done = std::move(j.done)] {
                            --busy_;
                            if (done)
                                done();
                            pump();
                        });
    }
}
BufferModel::BufferModel(EventBridge& b, EventBridge::Clock n, uint64_t cap)
    : bridge_(b), now_(std::move(n)), capacity_(cap) {
    if (!cap)
        throw Error(Code::InvalidArgument, "zero buffer");
}
void BufferModel::reserve(uint64_t bytes,
                          content::Context c,
                          std::string lifetime,
                          std::function<void(uint64_t)> done) {
    if (!bytes || !done)
        throw Error(Code::InvalidArgument, "invalid reservation", c);
    if (bytes > capacity_)
        throw Error(Code::CapacityExceeded, "single block exceeds buffer", c);
    if (next_ == UINT64_MAX)
        throw Error(Code::Overflow, "credit ID");
    c.time_ns = now_();
    queue_.push_back({++next_, bytes, c, std::move(lifetime), std::move(done)});
    pump();
}
void BufferModel::pump() {
    while (!queue_.empty() && queue_.front().bytes <= capacity_ - used_) {
        auto r = std::move(queue_.front());
        queue_.pop_front();
        used_ += r.bytes;
        peak_ = std::max(peak_, used_);
        wait_ns_ = checked_add(wait_ns_, now_() - r.context.time_ns);
        auto id = r.id;
        auto done = std::move(r.done);
        r.done = {};
        records_.push_back(
            {now_(), id, r.context.query, r.context.task, r.bytes, used_, true, r.lifetime});
        live_.emplace(id, std::move(r));
        bridge_.enqueue(
            now_(), EventBridge::ContinueSubmit, "credit granted", [done = std::move(done), id] {
                done(id);
            });
    }
}
void BufferModel::release(uint64_t id, uint64_t bytes) {
    auto it = live_.find(id);
    if (it == live_.end() || !bytes || bytes > it->second.bytes)
        throw Error(Code::InvalidArgument, "duplicate/invalid credit release");
    auto& r = it->second;
    r.bytes -= bytes;
    used_ -= bytes;
    records_.push_back(
        {now_(), id, r.context.query, r.context.task, bytes, used_, false, r.lifetime});
    if (!r.bytes)
        live_.erase(it);
    pump();
}
Controller::Controller(runtime::Runtime& r,
                       content::Handles& h,
                       const content::Layout& l,
                       LocalReads& reads,
                       transport::PcieTransport& p,
                       EventBridge& b,
                       EventBridge::Clock n,
                       ControllerConfig c,
                       Fault f)
    : runtime_(r), handles_(h), layout_(l), reads_(reads), transport_(p), bridge_(b), now_(n),
      config_(c), fault_(std::move(f)), buffer_(b, n, c.capacity_bytes), port_(b, n, 1),
      compute_(b, n, c.slots) {
    auto limits = bounds(c);
    query_bytes_ = limits.query;
    chunk_size_ = limits.chunk;
    block_bound_ = limits.block;
    admission_limit_ = limits.admission;
}
Controller::Bounds Controller::bounds(ControllerConfig c) {
    Bounds out{};
    if (!c.slots || !c.dimension || !c.staging_bytes_per_ns || !c.flops_per_ns || !c.chunk_limit ||
        !c.max_inflight || !c.vector_sector_bound || c.vector_sector_bound % 512 ||
        c.source.empty())
        throw Error(Code::InvalidArgument, "controller config units/rates/bounds/source");
    out.query = checked_mul(c.dimension, 4);
    auto fixed = checked_add(checked_add(out.query, 64), c.scratch_bytes);
    auto per = checked_add(c.vector_sector_bound, 16); // sector input + packed result + command ID
    if (fixed >= c.capacity_bytes || c.capacity_bytes - fixed < per)
        throw Error(Code::CapacityExceeded, "query plus minimum work block cannot fit");
    out.chunk = std::min(c.chunk_limit, (c.capacity_bytes - fixed) / per);
    out.block = checked_add(checked_add(64, c.scratch_bytes), checked_mul(out.chunk, per));
    out.admission = std::min(c.max_inflight, (c.capacity_bytes - out.block) / out.query);
    // Bound both Score and ScoreResult against native signed wire length before any upload.
    auto wire = [](uint64_t payload) {
        return checked_add(payload, checked_mul(ceil_div(payload, 128), 28));
    };
    if (wire(checked_add(64, out.query)) > INT32_MAX ||
        wire(checked_add(56, checked_mul(out.chunk, 12))) > INT32_MAX ||
        wire(checked_add(64, checked_mul(out.chunk, 4))) > INT32_MAX)
        throw Error(Code::CapacityExceeded, "configured chunk/query exceeds PCIe envelope bound");
    return out;
}

void Controller::admit() {
    while (admitted_ < admission_limit_ && !admissions_.empty()) {
        auto start = std::move(admissions_.front());
        admissions_.pop_front();
        ++admitted_;
        start();
    }
}
transport::Message Controller::message(Kind k, const Session& s, uint64_t task) const {
    transport::Message m;
    m.kind = k;
    m.query = s.query;
    m.task = task;
    m.token = k == Kind::QueryOpen ? 0 : s.token;
    return m;
}
void Controller::send(transport::Message m, std::function<void(transport::Message)> done) {
    Simulator->Synchronize_now(now_());
    transport_.send(m, [this, done = std::move(done)](auto, auto arrived) {
        conversation_.arrive(arrived);
        done(std::move(arrived));
    });
}
void Controller::inject(const std::string& stage, content::Context c) {
    c.time_ns = now_();
    if (fault_)
        fault_(stage, c);
}
void Controller::stage(content::Context c,
                       const std::string& name,
                       uint64_t bytes,
                       std::function<void()> done) {
    port_.submit(
        c, name, bytes, ceil_div(bytes, config_.staging_bytes_per_ns), {}, std::move(done));
}
runtime::Handle Controller::open(uint64_t q, uint64_t version, const std::vector<float>& values) {
    if (!q || !version || values.size() != config_.dimension || sessions_.count(q))
        throw Error(Code::InvalidArgument, "invalid/reused query open");
    auto s = std::make_shared<Session>();
    s->query = q;
    s->version = version;
    s->bytes = operators::fp32(values);
    auto request = message(Kind::QueryOpen, *s, 1);
    request.version = version;
    request.dimension = config_.dimension;
    request.query_values = values;
    transport::encode(request); // Finite values and complete envelope before credit.
    sessions_.emplace(q, s);
    runtime::Operation op;
    op.query = q;
    op.evaluate = [](const auto&) {
        return content::Bytes{};
    };
    op.service = [](auto) {};
    op.grant = [this, s, request](auto task) mutable {
        request.task = task;
        admissions_.push_back([this, s, request, task] {
            buffer_.reserve(
                query_bytes_,
                {s->query, task, now_()},
                "query",
                [this, s, request, task](auto credit) {
                    s->credit = credit;
                    send(request, [this, s, task](auto) {
                        try {
                            inject("open-command", {s->query, task, now_()});
                        } catch (const std::exception& e) {
                            fail_open(s, task, e);
                            return;
                        }
                        stage(
                            {s->query, task, now_()}, "query-write", query_bytes_, [this, s, task] {
                                try {
                                    inject("query-staged", {s->query, task, now_()});
                                } catch (const std::exception& e) {
                                    fail_open(s, task, e);
                                    return;
                                }
                                if (next_token_ == UINT64_MAX)
                                    throw Error(Code::Overflow, "query token");
                                s->token = ++next_token_;
                                send(message(Kind::QueryOpenAck, *s, task), [this, task](auto) {
                                    runtime_.start(task);
                                    runtime_.service_done(task);
                                });
                            });
                    });
                });
        });
        admit();
    };
    auto h = runtime_.submit(std::move(op));
    s->opened = runtime_.retain(h);
    return h;
}
void Controller::fail_open(const std::shared_ptr<Session>& s,
                           uint64_t task,
                           const std::exception& e) {
    auto detail = std::string(e.what());
    auto code = Code::BackendFailure;
    if (auto error = dynamic_cast<const Error*>(&e))
        code = error->code;
    auto m = message(Kind::Error, *s, task);
    m.token = 0;
    m.status = code == Code::InvalidArgument ? 1 : 5;
    send(m, [this, s, task, code, detail](auto) {
        buffer_.release(s->credit, query_bytes_);
        s->bytes.clear();
        runtime_.fail(task, code, detail);
        runtime_.release(s->opened);
        for (auto h : s->batches)
            runtime_.release(h);
        s->batches.clear();
        s->outstanding = 0;
        sessions_.erase(s->query);
        --admitted_;
        admit();
    });
}
void Controller::release_input(const std::shared_ptr<Chunk>& c) {
    if (c->data_live) {
        handles_.release(c->data);
        c->data_live = false;
    }
    auto amount = checked_add(c->input, c->scratch);
    if (amount) {
        buffer_.release(c->credit, amount);
        c->input = c->scratch = 0;
    }
}
void Controller::fail_chunk(const std::shared_ptr<Session>& s,
                            const std::shared_ptr<Chunk>& c,
                            const std::exception& e) {
    if (c->error)
        return;
    c->error = true;
    c->detail = e.what();
    if (auto err = dynamic_cast<const Error*>(&e))
        c->code = err->code;
    release_input(c); // Only called at safe boundaries with local reads/port access drained.
    auto m = message(Kind::Error, *s, c->task);
    m.status = c->code == Code::InvalidArgument ? 1 : c->code == Code::CapacityExceeded ? 3 : 5;
    send(m, [this, s, c](auto) {
        buffer_.release(c->credit, c->output);
        c->output = 0;
        c->result.clear();
        runtime_.fail(c->task, c->code, c->detail);
        --s->outstanding;
        close_ready(s);
    });
}
runtime::Handle Controller::chunk(const std::shared_ptr<Session>& s,
                                  std::vector<content::Selection> selections) {
    ++s->outstanding;
    auto c = std::make_shared<Chunk>();
    c->query = s->query;
    c->chunk = ++s->next_chunk;
    c->selections = std::move(selections);
    c->input = layout_.resolve(c->selections).sector_bytes;
    c->output = checked_mul(c->selections.size(), 12);
    c->scratch =
        checked_add(checked_add(64, checked_mul(c->selections.size(), 4)), config_.scratch_bytes);
    runtime::Operation op;
    op.query = s->query;
    op.inputs = {s->opened};
    op.needs_return = true;
    op.evaluate = [this, s, c](const auto&) {
        try {
            inject("evaluate", {s->query, c->task, now_()});
            auto vectors =
                handles_.try_get(c->data, content::Consumer::ControllerExecutor, s->version);
            std::vector<uint32_t> ids;
            for (auto& v : c->selections)
                ids.push_back(v.object.id);
            auto result = operators::squared_l2(s->bytes, vectors, ids);
            if (result.work.flops != checked_mul(checked_mul(ids.size(), config_.dimension), 3) ||
                result.work.output_bytes != c->output)
                throw Error(Code::InvalidArgument, "L2 work/buffer bound mismatch");
            c->result = std::move(result.bytes);
            return c->result;
        } catch (const std::exception& e) {
            c->detail = e.what();
            if (auto err = dynamic_cast<const Error*>(&e))
                c->code = err->code;
            return content::Bytes{};
        }
    };
    op.grant = [this, s, c](auto task) {
        c->task = task;
        buffer_.reserve(
            checked_add(checked_add(c->input, c->output), c->scratch),
            {s->query, task, now_()},
            "block",
            [this, s, c](auto credit) {
                c->credit = credit;
                auto m = message(Kind::Score, *s, c->task);
                m.chunk = c->chunk;
                m.dimension = config_.dimension;
                for (auto& v : c->selections)
                    m.ids.push_back(v.object.id);
                send(m, [this, s, c](auto) {
                    try {
                        inject("command", {s->query, c->task, now_()});
                    } catch (const std::exception& e) {
                        fail_chunk(s, c, e);
                        return;
                    }
                    c->data = handles_.create(c->selections,
                                              content::Location::DeviceBuffer,
                                              {s->query, c->task, now_()});
                    c->data_live = true;
                    // An exception after backend injection is fatal; never free credit under live I/O.
                    try {
                        reads_.submit(
                            layout_,
                            c->selections,
                            {s->query, c->task, now_()},
                            [this, s, c](auto completion) {
                                uint64_t transactions = 0;
                                for (auto& request : completion.requests)
                                    transactions += request.transactions.size();
                                storage_.push_back({s->query,
                                                    c->task,
                                                    completion.context.time_ns,
                                                    now_(),
                                                    completion.plan.sector_bytes,
                                                    completion.requests.size(),
                                                    transactions});
                                stage({s->query, c->task, now_()},
                                      "input-write",
                                      c->input,
                                      [this, s, c] {
                                          handles_.publish(c->data);
                                          try {
                                              inject("staged", {s->query, c->task, now_()});
                                          } catch (const std::exception& e) {
                                              fail_chunk(s, c, e);
                                              return;
                                          }
                                          stage(
                                              {s->query, c->task, now_()},
                                              "compute-read",
                                              checked_add(c->input, query_bytes_),
                                              [this, s, c] {
                                                  auto flops =
                                                      checked_mul(checked_mul(c->selections.size(),
                                                                              config_.dimension),
                                                                  3);
                                                  auto duration = checked_add(
                                                      config_.launch_ns,
                                                      ceil_div(flops, config_.flops_per_ns));
                                                  compute_.submit(
                                                      {s->query, c->task, now_()},
                                                      "arithmetic",
                                                      flops,
                                                      duration,
                                                      [this, c] {
                                                          runtime_.start(c->task);
                                                      },
                                                      [this, s, c] {
                                                          if (!c->detail.empty()) {
                                                              Error e(c->code, c->detail);
                                                              fail_chunk(s, c, e);
                                                              return;
                                                          }
                                                          release_input(c);
                                                          stage({s->query, c->task, now_()},
                                                                "result-write",
                                                                c->output,
                                                                [this, s, c] {
                                                                    try {
                                                                        inject("result",
                                                                               {s->query,
                                                                                c->task,
                                                                                now_()});
                                                                    } catch (
                                                                        const std::exception& e) {
                                                                        fail_chunk(s, c, e);
                                                                        return;
                                                                    }
                                                                    stage(
                                                                        {s->query, c->task, now_()},
                                                                        "result-read",
                                                                        c->output,
                                                                        [this, c] {
                                                                            runtime_.service_done(
                                                                                c->task);
                                                                        });
                                                                });
                                                      });
                                              });
                                      });
                            });
                    } catch (const Error& e) {
                        if (e.code == Code::BackendFailure)
                            throw;
                        fail_chunk(s, c, e);
                    }
                });
            });
    };
    op.service = [](auto) {};
    op.send_return = [this, s, c](auto, size_t) {
        auto m = message(Kind::ScoreResult, *s, c->task);
        m.chunk = c->chunk;
        for (auto score : operators::scores(c->result))
            m.scores.push_back({score.id, score.distance});
        send(m, [this, s, c](auto) {
            buffer_.release(c->credit, c->output);
            c->output = 0;
            c->result.clear();
            runtime_.return_done(c->task);
            --s->outstanding;
            close_ready(s);
        });
    };
    return runtime_.submit(std::move(op));
}
runtime::Handle Controller::score(uint64_t q, const std::vector<content::Selection>& selections) {
    auto it = sessions_.find(q);
    if (it == sessions_.end() || it->second->closing || selections.empty())
        throw Error(Code::InvalidArgument, "score without open/after close/empty");
    auto s = it->second;
    std::set<uint32_t> ids;
    // Validate the entire semantic batch before creating chunks, tasks, messages or credit.
    for (auto& v : selections) {
        if (v.object.kind != content::Kind::Vector || v.object.version != s->version ||
            v.range.offset || v.range.length != query_bytes_ || !ids.insert(v.object.id).second)
            throw Error(Code::InvalidArgument, "score vector/version/dimension/duplicate ID");
        if (layout_.resolve({v}).sector_bytes > config_.vector_sector_bound)
            throw Error(Code::CapacityExceeded,
                        "vector exceeds configured single-block sector bound");
    }
    std::vector<runtime::Handle> chunks;
    for (size_t i = 0; i < selections.size(); i += chunk_size_) {
        auto end = std::min(selections.size(), i + size_t(chunk_size_));
        chunks.push_back(chunk(s, {selections.begin() + i, selections.begin() + end}));
    }
    runtime::Operation op;
    op.query = q;
    op.inputs = chunks;
    op.grant = [this](auto id) {
        runtime_.start(id);
    };
    op.service = [this](auto id) {
        runtime_.service_done(id);
    };
    op.evaluate = [](const auto& values) {
        content::Bytes bytes;
        for (auto& v : values)
            bytes.insert(bytes.end(), v.begin(), v.end());
        return bytes;
    };
    auto h = runtime_.submit(std::move(op));
    for (auto c : chunks)
        runtime_.release(c);
    s->batches.push_back(runtime_.retain(h));
    return h;
}
runtime::Handle Controller::close(uint64_t q) {
    auto it = sessions_.find(q);
    if (it == sessions_.end() || it->second->closing)
        throw Error(Code::InvalidArgument, "invalid/repeated close");
    auto s = it->second;
    s->closing = true;
    runtime::Operation op;
    op.query = q;
    op.inputs = {s->opened};
    op.evaluate = [](const auto&) {
        return content::Bytes{};
    };
    op.service = [](auto) {};
    op.grant = [this, s](auto task) {
        s->close_task = task;
        close_ready(s);
    };
    return runtime_.submit(std::move(op));
}
void Controller::close_ready(const std::shared_ptr<Session>& s) {
    if (!s->close_task || s->outstanding)
        return;
    auto task = s->close_task;
    s->close_task = 0;
    send(message(Kind::QueryClose, *s, task), [this, s, task](auto) {
        send(message(Kind::QueryCloseAck, *s, task), [this, s, task](auto) {
            buffer_.release(s->credit, query_bytes_);
            s->bytes.clear();
            runtime_.release(s->opened);
            for (auto h : s->batches)
                runtime_.release(h);
            s->batches.clear();
            sessions_.erase(s->query);
            --admitted_;
            admit();
            runtime_.start(task);
            runtime_.service_done(task);
        });
    });
}

std::string Controller::diagnostic() const {
    std::ostringstream o;
    o << "controller sessions=" << sessions_.size() << " admissions=" << admissions_.size()
      << " used=" << buffer_.used() << " credit_wait=" << buffer_.waiting()
      << " port=" << port_.pending() << " compute=" << compute_.pending()
      << " messages=" << transport_.pending();
    return o.str();
}
} // namespace ndpsim::model
