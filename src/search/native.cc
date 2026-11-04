#include "search/core.h"

#include <algorithm>
#include <array>
#include <charconv>
#include <condition_variable>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <mutex>
#include <sys/resource.h>
#include <thread>
#include <time.h>

namespace {
using namespace ndpsim;
using content::Bytes;

uint32_t u32(const uint8_t* bytes) {
    uint32_t value = 0;
    for (unsigned i = 0; i < 4; ++i)
        value |= uint32_t(bytes[i]) << (8 * i);
    return value;
}

std::vector<uint32_t> ids(const uint8_t* bytes, size_t count) {
    std::vector<uint32_t> result;
    result.reserve(count);
    for (size_t i = 0; i < count; ++i)
        result.push_back(u32(bytes + 4 * i));
    return result;
}

uint32_t number(const char* arg) {
    uint32_t value = 0;
    const auto end = arg + std::char_traits<char>::length(arg);
    const auto parsed = std::from_chars(arg, end, value);
    if (parsed.ec != std::errc{} || parsed.ptr != end)
        throw std::runtime_error("expected an unsigned 32-bit integer");
    return value;
}

struct Config {
    size_t count, dimensions, queries, degree, truth_k;
    size_t entry, k, width, batch, budget, chunk, workers, concurrency;

    explicit Config(char** a)
        : count(number(a[1])), dimensions(number(a[2])), queries(number(a[3])),
          degree(number(a[4])), truth_k(number(a[5])), entry(number(a[6])),
          k(number(a[7])), width(number(a[8])), batch(number(a[9])),
          budget(number(a[10])), chunk(number(a[11])), workers(number(a[12])),
          concurrency(number(a[13])) {
        if (!count || !dimensions || !queries || !degree || entry >= count ||
            !k || k > count || k > truth_k || k > width || !batch || !budget ||
            !chunk || (workers != 1 && workers != 2 && workers != 4) || concurrency < workers)
            throw std::runtime_error("invalid search shape or worker configuration");
    }
};

Bytes load(const char* path, const char* hash, size_t rows, size_t stride) {
    if (rows > size_t(std::numeric_limits<std::streamsize>::max()) / stride)
        throw std::runtime_error("input too large");
    const auto size = static_cast<std::streamsize>(rows * stride);
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    if (!stream || stream.tellg() != size)
        throw std::runtime_error(std::string("input size mismatch: ") + path);
    stream.seekg(0);
    Bytes bytes(size);
    if (!stream.read(reinterpret_cast<char*>(bytes.data()), size) ||
        stream.peek() != std::char_traits<char>::eof() || content::sha256(bytes) != hash)
        throw std::runtime_error(std::string("input read or SHA256 mismatch: ") + path);
    return bytes;
}

struct Input {
    Bytes vectors, queries, graph, truth, query_ids;
    Input(const Config& c, char** a)
        : vectors(load(a[14], a[15], c.count, c.dimensions * 4)),
          queries(load(a[16], a[17], c.queries, c.dimensions * 4)),
          graph(load(a[18], a[19], c.count, (c.degree + 1) * 4)),
          truth(load(a[20], a[21], c.queries, c.truth_k * 4)),
          query_ids(load(a[22], a[23], c.queries, 4)) {
        // The public launcher validates dataset/index/truth identity and payloads.
        // Validate row bounds here too, before executing any query.
        for (size_t node = 0; node < c.count; ++node) {
            const auto* row = graph.data() + node * (c.degree + 1) * 4;
            const size_t degree = u32(row);
            if (degree > c.degree)
                throw std::runtime_error("invalid adjacency degree");
            for (size_t i = 1; i <= degree; ++i)
                if (u32(row + i * 4) >= c.count)
                    throw std::runtime_error("invalid adjacency ID");
        }
    }
};

constexpr std::array work_fields{
    &operators::Work::elements, &operators::Work::flops, &operators::Work::read_bytes,
    &operators::Work::write_bytes, &operators::Work::output_bytes,
    &operators::Work::candidate_checks, &operators::Work::comparisons};
constexpr std::array work_names{"elements", "flops", "read_bytes", "write_bytes",
                                "output_bytes", "candidate_checks", "comparisons"};

struct Result {
    size_t worker = 0, expanded = 0, scored = 0, rounds = 0, chunks = 0;
    uint64_t admission = 0, start = 0, end = 0, cpu = 0;
    std::vector<operators::Score> top;
    operators::Work work;
    void add(const operators::Work& delta) {
        for (auto field : work_fields)
            work.*field += delta.*field;
    }
};

Result search_query(const Config& c, const Input& input, size_t query_id) {
    Result result;
    const auto stride = c.dimensions * 4;
    const auto* q = input.queries.data() + query_id * stride;
    const Bytes query(q, q + stride);
    std::set<uint32_t> scored, expanded;
    Bytes ranked;
    std::vector<uint32_t> fresh{static_cast<uint32_t>(c.entry)};
    for (;;) {
        if (!fresh.empty()) {
            Bytes batch;
            for (size_t offset = 0; offset < fresh.size(); offset += c.chunk) {
                const auto end = std::min(fresh.size(), offset + c.chunk);
                const std::vector<uint32_t> group(fresh.begin() + offset, fresh.begin() + end);
                std::vector<Bytes> vectors;
                vectors.reserve(group.size());
                for (auto id : group) {
                    const auto* v = input.vectors.data() + id * stride;
                    vectors.emplace_back(v, v + stride);
                    scored.insert(id);
                }
                auto scores = operators::squared_l2(query, vectors, group);
                result.add(scores.work);
                batch.insert(batch.end(), scores.bytes.begin(), scores.bytes.end());
                ++result.chunks;
            }
            auto merged = search::core::merge_candidates(std::move(ranked), batch, c.count);
            result.add(merged.work);
            ranked = std::move(merged.bytes);
        }
        auto selected = search::core::select_frontier(ranked, expanded, c.width, c.batch, c.budget);
        result.add(selected.work);
        if (selected.bytes.empty())
            break;
        ++result.rounds;
        std::vector<std::vector<uint32_t>> rows;
        for (auto id : ids(selected.bytes.data(), selected.bytes.size() / 4)) {
            expanded.insert(id);
            const auto* row = input.graph.data() + id * (c.degree + 1) * 4;
            rows.push_back(ids(row + 4, u32(row)));
        }
        auto dedup = search::core::dedup_neighbors(rows, scored, c.count);
        result.add(dedup.work);
        fresh = ids(dedup.bytes.data(), dedup.bytes.size() / 4);
    }
    ranked.resize(std::min(c.k, ranked.size() / 12) * 12);
    result.top = operators::scores(ranked);
    result.scored = scored.size();
    result.expanded = expanded.size();
    return result;
}

uint64_t nanos(const timespec& ts) {
    return uint64_t(ts.tv_sec) * 1000000000 + ts.tv_nsec;
}

uint64_t clock_ns(clockid_t clock) {
    timespec ts{};
    if (clock_gettime(clock, &ts))
        throw std::runtime_error("clock_gettime failed");
    return nanos(ts);
}

uint64_t resolution(clockid_t clock) {
    timespec ts{};
    if (clock_getres(clock, &ts))
        throw std::runtime_error("clock_getres failed");
    return nanos(ts);
}

rusage usage() {
    rusage value{};
    if (getrusage(RUSAGE_SELF, &value))
        throw std::runtime_error("getrusage failed");
    return value;
}

class Batch {
public:
    Batch(const Config& c, const Input& input)
        : config(c), data(input), admissions(c.queries), admitted(std::min(c.concurrency, c.queries)),
          results(c.queries) {}

    void run() {
        std::vector<std::thread> threads;
        try {
            for (size_t i = 0; i < config.workers; ++i)
                threads.emplace_back(&Batch::worker, this, i);
            std::unique_lock<std::mutex> lock(mutex);
            changed.wait(lock, [&] { return failure || ready == config.workers; });
            if (!failure) {
                before = usage();
                begin = clock_ns(CLOCK_MONOTONIC);
                started = true;
                changed.notify_all();
                changed.wait(lock, [&] { return failure || completed == config.queries; });
            }
        } catch (...) {
            fail(std::current_exception());
        }
        for (auto& thread : threads)
            thread.join();
        if (failure)
            std::rethrow_exception(failure);
    }

    void report() const {
        const auto wall = resolution(CLOCK_MONOTONIC);
        const auto cpu = resolution(CLOCK_THREAD_CPUTIME_ID);
        const auto minor = after.ru_minflt - before.ru_minflt;
        const auto major = after.ru_majflt - before.ru_majflt;
        std::vector<uint64_t> latencies;
        uint64_t cpu_total = 0;
        std::cout << std::setprecision(17)
                  << "{\"schema\":\"ndpsim-native-batch-v1\",\"wall_clock\":\"CLOCK_MONOTONIC\""
                  << ",\"cpu_clock\":\"CLOCK_THREAD_CPUTIME_ID\",\"wall_resolution_ns\":" << wall
                  << ",\"cpu_resolution_ns\":" << cpu << ",\"workers\":" << config.workers
                  << ",\"concurrency\":" << config.concurrency << ",\"minor_faults\":" << minor
                  << ",\"major_faults\":" << major << ",\"paging_eligible\":" << (major == 0 ? "true" : "false")
                  << ",\"queries\":[";
        for (size_t i = 0; i < results.size(); ++i) {
            const auto& r = results[i];
            const auto truth = ids(data.truth.data() + i * config.truth_k * 4, config.k);
            size_t hits = 0;
            bool first = false;
            const auto latency = r.end - r.admission;
            latencies.push_back(latency);
            cpu_total += r.cpu;
            std::cout << (i ? ",{" : "{") << "\"query_id\":" << i
                      << ",\"original_query_id\":" << u32(data.query_ids.data() + i * 4)
                      << ",\"worker\":" << r.worker << ",\"offered_ns\":0,\"admission_ns\":" << r.admission
                      << ",\"worker_start_ns\":" << r.start << ",\"visible_ns\":" << r.end
                      << ",\"admission_wait_ns\":" << r.admission << ",\"worker_wait_ns\":" << r.start - r.admission
                      << ",\"latency_ns\":" << latency << ",\"offered_latency_ns\":" << r.end
                      << ",\"thread_cpu_ns\":" << r.cpu << ",\"expanded\":" << r.expanded
                      << ",\"scored\":" << r.scored << ",\"expansion_batches\":" << r.rounds
                      << ",\"score_chunks\":" << r.chunks << ",\"work\":{";
            for (size_t f = 0; f < work_fields.size(); ++f)
                std::cout << (f ? ",\"" : "\"") << work_names[f] << "\":" << r.work.*work_fields[f];
            std::cout << "},\"top_k\":[";
            for (size_t j = 0; j < r.top.size(); ++j) {
                const auto& score = r.top[j];
                hits += std::find(truth.begin(), truth.end(), score.id) != truth.end();
                first |= score.id == truth.front();
                std::cout << (j ? ",{" : "{") << "\"id\":" << score.id
                          << ",\"distance\":" << score.distance << '}';
            }
            std::cout << "],\"recall_at_k\":" << double(hits) / config.k
                      << ",\"one_recall_at_r\":" << int(first)
                      << ",\"insufficient_results\":" << (r.top.size() < config.k ? "true" : "false") << '}';
        }
        std::sort(latencies.begin(), latencies.end());
        const auto percentile = [&](size_t p) { return latencies[(p * latencies.size() + 99) / 100 - 1]; };
        std::cout << "],\"completed\":" << completed << ",\"window_ns\":" << end
                  << ",\"drain_end_ns\":" << end << ",\"thread_cpu_ns\":" << cpu_total
                  << ",\"worker_cpu_fraction\":" << double(cpu_total) / end / config.workers
                  << ",\"qps\":" << double(completed) * 1e9 / end
                  << ",\"p50_ns\":" << percentile(50) << ",\"p95_ns\":" << percentile(95)
                  << ",\"exit_code\":0}\n";
        std::cout.flush();
        if (!std::cout)
            throw std::runtime_error("could not write batch report");
    }

private:
    void fail(std::exception_ptr error) {
        std::lock_guard<std::mutex> lock(mutex);
        if (!failure)
            failure = error;
        changed.notify_all();
    }

    void worker(size_t id) {
        try {
            search_query(config, data, id % config.queries);
            std::unique_lock<std::mutex> lock(mutex);
            ++ready;
            changed.notify_all();
            for (;;) {
                changed.wait(lock, [&] { return failure || (started && (next < admitted || next == config.queries)); });
                if (failure || next == config.queries)
                    return;
                const auto query = next++;
                const auto admission = admissions[query];
                const auto start = clock_ns(CLOCK_MONOTONIC) - begin;
                lock.unlock();
                const auto cpu_start = clock_ns(CLOCK_THREAD_CPUTIME_ID);
                auto result = search_query(config, data, query);
                result.cpu = clock_ns(CLOCK_THREAD_CPUTIME_ID) - cpu_start;
                result.worker = id;
                result.admission = admission;
                result.start = start;
                lock.lock();
                // Publish atomically with completion and admit the next offered query.
                result.end = clock_ns(CLOCK_MONOTONIC) - begin;
                end = result.end;
                results[query] = std::move(result);
                if (admitted < config.queries)
                    admissions[admitted++] = end;
                if (++completed == config.queries)
                    after = usage();
                changed.notify_all();
            }
        } catch (...) {
            fail(std::current_exception());
        }
    }

    const Config& config;
    const Input& data;
    std::mutex mutex;
    std::condition_variable changed;
    std::exception_ptr failure;
    bool started = false;
    size_t ready = 0, next = 0, completed = 0;
    std::vector<uint64_t> admissions;
    size_t admitted;
    std::vector<Result> results;
    uint64_t begin = 0, end = 0;
    rusage before{}, after{};
};
} // namespace

int main(int argc, char** argv) {
    try {
        if (argc != 24)
            throw std::runtime_error("use tools/run_native.py for validated dataset-v2 input");
        const Config config(argv);
        const Input input(config, argv);
        Batch batch(config, input);
        batch.run();
        batch.report();
    } catch (const std::exception& e) {
        std::cerr << "ndpsim_native: " << e.what() << '\n';
        return 1;
    }
}
