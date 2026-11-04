#include "search/native_observation.h"

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

uint64_t ndpsim::search::native::clock_ns(clockid_t clock) {
    timespec ts{};
    if (clock_gettime(clock, &ts))
        throw std::runtime_error("clock_gettime failed");
    return uint64_t(ts.tv_sec) * 1000000000 + ts.tv_nsec;
}

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

using namespace ndpsim::search::native;

struct Result {
    size_t worker = 0, expanded = 0, scored = 0, rounds = 0, chunks = 0, score_batches = 0;
    uint64_t admission = 0, start = 0, end = 0, cpu = 0, cpu_start = 0;
    std::vector<operators::Score> top;
    operators::Work work;
    std::vector<std::vector<operators::Score>> score_trace;
    std::vector<std::vector<uint32_t>> expansion_trace;
    void add(const operators::Work& delta) {
        add_work(work, delta);
    }
};

std::vector<uint32_t> score_ids(const Bytes& packed) {
    std::vector<uint32_t> result;
    for (size_t i = 0; i < packed.size(); i += 12)
        result.push_back(u32(packed.data() + i));
    return result;
}

template<Mode mode>
Result search_query(const Config& c, const Input& input, size_t query_id,
                    bool trace = false, Recorder* observer = nullptr) {
    constexpr bool cpu = mode == Mode::cpu_sample;
    constexpr bool capture = mode == Mode::static_capture;
    const auto take = [&](Bucket bucket) { return cpu && observer->take(bucket); };
    const auto tick = [&](bool enabled) { return cpu ? observer->tick(enabled) : uint64_t{0}; };
    Result result;
    size_t op = 1;
    auto start = tick(take(Bucket::init));
    // The scope ends in the finish bucket, including private state destruction.
    {
        const auto stride = c.dimensions * 4;
        const auto* q = input.queries.data() + query_id * stride;
        const Bytes query(q, q + stride);
        std::set<uint32_t> scored, expanded;
        Bytes ranked;
        std::vector<uint32_t> fresh{static_cast<uint32_t>(c.entry)};
        if constexpr (cpu)
            observer->sample("init", op, -1, 0, start, tick(start != 0), {}, stride);
        if constexpr (capture)
            observer->event("INIT");
        for (;;) {
            if (!fresh.empty()) {
                ++result.score_batches;
                op += 2; // MARK_SCORED, SCORE_L2
                if constexpr (capture)
                    observer->event("MARK_SCORED", fresh);
                Bytes batch;
                operators::Work score_work;
                size_t chunk = 0;
                for (size_t offset = 0; offset < fresh.size(); offset += c.chunk, ++chunk) {
                    const bool sample_score = take(Bucket::score);
                    const bool sample_merge = take(Bucket::merge);
                    const auto prepare_start = tick(sample_score);
                    const auto end = std::min(fresh.size(), offset + c.chunk);
                    uint64_t prepared, scored_at, merged_at;
                    operators::Work l2_work;
                    {
                        const std::vector<uint32_t> group(fresh.begin() + offset, fresh.begin() + end);
                        std::vector<Bytes> vectors;
                        vectors.reserve(group.size());
                        for (auto id : group) {
                            const auto* v = input.vectors.data() + id * stride;
                            vectors.emplace_back(v, v + stride);
                            scored.insert(id);
                        }
                        prepared = tick(sample_score);
                        auto scores = operators::squared_l2(query, vectors, group);
                        scored_at = tick(sample_score || sample_merge);
                        l2_work = scores.work;
                        batch.insert(batch.end(), scores.bytes.begin(), scores.bytes.end());
                    }
                    merged_at = tick(sample_merge);
                    result.add(l2_work);
                    if constexpr (capture)
                        add_work(score_work, l2_work);
                    if (trace) {
                        Bytes packed(batch.end() - (end - offset) * 12, batch.end());
                        result.score_trace.push_back(operators::scores(packed));
                    }
                    if constexpr (cpu) {
                        const auto n = end - offset;
                        operators::Work preparation;
                        preparation.read_bytes = n * stride;
                        observer->sample("score_prepare", op, chunk, 0, prepare_start, prepared,
                                         preparation, n, c.dimensions);
                        observer->sample("score_l2", op, chunk, 0, prepared, scored_at,
                                         l2_work, n, c.dimensions);
                        observer->sample("merge", op + 1, -1, chunk, sample_merge ? scored_at : 0, merged_at,
                                         {}, 0, n);
                    }
                    ++result.chunks;
                }
                if constexpr (capture)
                    observer->event("SCORE_L2", fresh, batch, fresh.size(), score_work);
                ++op;
                const auto previous = ranked.size() / 12;
                start = tick(take(Bucket::merge));
                operators::Work merge_work;
                {
                    auto merged = search::core::merge_candidates(std::move(ranked), batch, c.count);
                    merge_work = merged.work;
                    ranked = std::move(merged.bytes);
                    Bytes{}.swap(batch);
                }
                const auto end = tick(start != 0);
                result.add(merge_work);
                if constexpr (cpu)
                    observer->sample("merge", op, -1, chunk, start, end, merge_work, previous, fresh.size());
                if constexpr (capture)
                    observer->event("MERGE_CANDIDATES", score_ids(ranked), ranked, ranked.size()/12, merge_work);
            }
            ++op;
            start = tick(take(Bucket::select));
            auto selected = search::core::select_frontier(ranked, expanded, c.width, c.batch, c.budget);
            const auto chosen = ids(selected.bytes.data(), selected.bytes.size() / 4);
            const auto selected_at = tick(start != 0);
            result.add(selected.work);
            if constexpr (cpu)
                observer->sample("select", op, -1, 0, start, selected_at, selected.work,
                                 ranked.size()/12, expanded.size());
            if constexpr (capture)
                observer->event("SELECT_FRONTIER", chosen, selected.bytes, chosen.size(), selected.work);
            if (trace)
                result.expansion_trace.push_back(chosen);
            if (chosen.empty())
                break;
            ++result.rounds;
            ++op; // MARK_EXPANDED
            if constexpr (capture)
                observer->event("MARK_EXPANDED", chosen);
            std::vector<std::vector<uint32_t>> rows;
            Bytes joined;
            for (auto id : chosen) {
                ++op;
                start = tick(take(Bucket::adjacency));
                expanded.insert(id);
                const auto* row = input.graph.data() + id * (c.degree + 1) * 4;
                rows.push_back(ids(row + 4, u32(row)));
                const auto end = tick(start != 0);
                if constexpr (cpu) {
                    operators::Work preparation;
                    preparation.read_bytes = 4 * (1 + rows.back().size());
                    observer->sample("adjacency_prepare", op, -1, 0, start, end, preparation,
                                     rows.back().size());
                }
                if constexpr (capture) {
                    Bytes payload(row, row + 4 * (1 + rows.back().size()));
                    observer->event("READ_ADJACENCY", {id}, payload, rows.back().size());
                    joined.insert(joined.end(), payload.begin(), payload.end());
                }
            }
            ++op; // JOIN
            if constexpr (capture)
                observer->event("JOIN", chosen, joined, rows.size());
            ++op;
            start = tick(take(Bucket::dedup));
            auto dedup = search::core::dedup_neighbors(rows, scored, c.count);
            fresh = ids(dedup.bytes.data(), dedup.bytes.size() / 4);
            decltype(rows){}.swap(rows);
            const auto dedup_at = tick(start != 0);
            result.add(dedup.work);
            if constexpr (cpu)
                observer->sample("dedup", op, -1, 0, start, dedup_at, dedup.work,
                                 dedup.work.candidate_checks, scored.size());
            if constexpr (capture)
                observer->event("DEDUP_NEIGHBORS", fresh, dedup.bytes, fresh.size(), dedup.work);
        }
        ++op;
        start = tick(take(Bucket::finish));
        ranked.resize(std::min(c.k, ranked.size() / 12) * 12);
        result.top = operators::scores(ranked);
        result.scored = scored.size();
        result.expanded = expanded.size();
        if constexpr (capture)
            observer->event("FINISH", score_ids(ranked), ranked, ranked.size()/12);
    }
    if constexpr (cpu)
        observer->sample("finish", op, -1, 0, start, tick(start != 0), {}, result.top.size());
    return result;
}

uint64_t nanos(const timespec& ts) {
    return uint64_t(ts.tv_sec) * 1000000000 + ts.tv_nsec;
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
    Batch(const Config& c, const Input& input, bool trace, Observation& observation)
        : config(c), data(input), trace(trace), observation(observation), admissions(c.queries), admitted(std::min(c.concurrency, c.queries)),
          results(c.queries), prefixes(observation.mode == Mode::cpu_sample && observation.stride > 1 ?
                                       c.queries : 0) {}

    void run() {
        std::vector<std::thread> threads;
        try {
            for (size_t i = 0; i < config.workers; ++i)
                threads.emplace_back(&Batch::worker, this, i);
            std::unique_lock<std::mutex> lock(mutex);
            changed.wait(lock, [&] { return failure || ready == config.workers; });
            if (!failure) {
                Inventory prefix{};
                for (auto& counts : prefixes) {
                    const auto local = counts;
                    counts = prefix;
                    for (size_t i = 0; i < prefix.size(); ++i)
                        prefix[i] += local[i];
                }
                total_inventory = prefix;
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
                      << ",\"cpu_start_ns\":" << r.cpu_start
                      << ",\"thread_cpu_ns\":" << r.cpu << ",\"expanded\":" << r.expanded
                      << ",\"scored\":" << r.scored << ",\"expansion_batches\":" << r.rounds
                      << ",\"score_batches\":" << r.score_batches
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
            if (trace || i < observation.detail) {
                std::cout << "],\"score_trace\":[";
                for (size_t j = 0; j < r.score_trace.size(); ++j) {
                    std::cout << (j ? ",[" : "[");
                    const auto& chunk = r.score_trace[j];
                    for (size_t n = 0; n < chunk.size(); ++n)
                        std::cout << (n ? ",[" : "[") << chunk[n].id << ',' << chunk[n].distance << ']';
                    std::cout << ']';
                }
                std::cout << "],\"expansion_trace\":[";
                for (size_t j = 0; j < r.expansion_trace.size(); ++j) {
                    std::cout << (j ? ",[" : "[");
                    const auto& chosen = r.expansion_trace[j];
                    for (size_t n = 0; n < chosen.size(); ++n)
                        std::cout << (n ? "," : "") << chosen[n];
                    std::cout << ']';
                }
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
                  << ",\"empty_probe_ns\":" << probes << ",\"empty_probe_count\":"
                  << (observation.mode == Mode::cpu_sample ? config.workers * probe_count : 0)
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
            search_query<Mode::benchmark>(config, data, id % config.queries);
            for (size_t query = id; query < prefixes.size(); query += config.workers) {
                const auto r = search_query<Mode::benchmark>(config, data, query);
                prefixes[query] = inventory(r);
            }
            uint64_t overhead = 0;
            if (observation.mode == Mode::cpu_sample) {
                for (size_t i = 0; i < probe_count; ++i) {
                    const auto start = clock_ns(CLOCK_THREAD_CPUTIME_ID);
                    overhead += clock_ns(CLOCK_THREAD_CPUTIME_ID) - start;
                }
            }
            std::unique_lock<std::mutex> lock(mutex);
            probes += overhead;
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
                Recorder observer(observation, prefixes.empty() ? Inventory{} : prefixes[query]);
                const bool detail = trace || query < observation.detail;
                const auto cpu_start = observation.mode == Mode::uninstrumented ? 0 :
                    clock_ns(CLOCK_THREAD_CPUTIME_ID);
                Result result;
                switch (observation.mode) {
                case Mode::static_capture:
                    result = search_query<Mode::static_capture>(config, data, query, detail, &observer);
                    break;
                case Mode::cpu_sample:
                    result = search_query<Mode::cpu_sample>(config, data, query, detail, &observer);
                    break;
                default:
                    result = search_query<Mode::benchmark>(config, data, query, detail);
                }
                if (!prefixes.empty()) {
                    auto actual = inventory(result);
                    for (size_t i = 0; i < actual.size(); ++i)
                        actual[i] += prefixes[query][i];
                    if (actual != (query + 1 == prefixes.size() ? total_inventory : prefixes[query + 1]))
                        throw std::runtime_error("CPU sampling plan disagrees with actual query inventory");
                }
                result.cpu_start = cpu_start;
                if (observation.mode != Mode::uninstrumented)
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
                if (observation.mode == Mode::static_capture || observation.mode == Mode::cpu_sample) {
                    lock.unlock();
                    observer.flush(query);
                    lock.lock();
                }
            }
        } catch (...) {
            fail(std::current_exception());
        }
    }

    const Config& config;
    const Input& data;
    const bool trace;
    Observation& observation;
    static constexpr size_t probe_count = 256;
    uint64_t probes = 0;
    std::mutex mutex;
    std::condition_variable changed;
    std::exception_ptr failure;
    bool started = false;
    size_t ready = 0, next = 0, completed = 0;
    std::vector<uint64_t> admissions;
    size_t admitted;
    std::vector<Result> results;
    std::vector<Inventory> prefixes;
    Inventory total_inventory{};
    static Inventory inventory(const Result& r) {
        return {1, r.chunks, r.chunks + r.score_batches, r.rounds + 1, r.expanded, r.rounds, 1};
    }
    uint64_t begin = 0, end = 0;
    rusage before{}, after{};
};
} // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--build-info") {
            std::cout << NDPSIM_NATIVE_SOURCE_SHA256 << '\n' << __VERSION__ << '\n'
                      << NDPSIM_NATIVE_FLAGS << '\n';
            return 0;
        }
        const bool trace = argc > 24 && std::string(argv[argc-1]) == "--semantic-log";
        const bool detail = argc > 24 && std::string(argv[argc-1]) == "--detail8";
        const int count = argc - trace - detail;
        if (count != 24 && count != 25 && count != 29)
            throw std::runtime_error("use tools/run_native.py for validated dataset-v2 input");
        Observation observation;
        observation.detail = detail ? 8 : 0;
        if (count == 25 && std::string(argv[24]) == "uninstrumented")
            observation.mode = Mode::uninstrumented;
        else if (count == 29) {
            const std::string mode = argv[24];
            if (mode == "cpu_sample")
                observation.mode = Mode::cpu_sample;
            else if (mode == "static_capture")
                observation.mode = Mode::static_capture;
            else
                throw std::runtime_error("invalid observation mode");
            observation.directory = argv[25];
            observation.remaining = number(argv[26]);
            observation.buffer_limit = number(argv[27]);
            observation.stride = number(argv[28]);
            if (!observation.stride)
                throw std::runtime_error("invalid CPU sample stride");
        } else if (count != 24) {
            throw std::runtime_error("invalid observation mode");
        }
        const Config config(argv);
        const Input input(config, argv);
        Batch batch(config, input, trace, observation);
        batch.run();
        batch.report();
    } catch (const std::exception& e) {
        std::cerr << "ndpsim_native: " << e.what() << '\n';
        return 1;
    }
}
