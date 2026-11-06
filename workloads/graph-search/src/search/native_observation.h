#pragma once

#include "search/core.h"
#include <array>
#include <atomic>
#include <fstream>
#include <sstream>
#include <time.h>

namespace ndpsim::search::native {
using content::Bytes;

constexpr std::array work_fields{
    &operators::Work::elements, &operators::Work::flops, &operators::Work::read_bytes,
    &operators::Work::write_bytes, &operators::Work::output_bytes,
    &operators::Work::candidate_checks, &operators::Work::comparisons};
constexpr std::array work_names{"elements", "flops", "read_bytes", "write_bytes",
                                "output_bytes", "candidate_checks", "comparisons"};
inline void add_work(operators::Work& total, const operators::Work& delta) {
    for (auto field : work_fields)
        total.*field += delta.*field;
}

uint64_t clock_ns(clockid_t clock);

enum class Mode { benchmark, uninstrumented, cpu_sample, static_capture };

struct Observation {
    Mode mode = Mode::benchmark;
    std::string directory;
    size_t buffer_limit = 0, stride = 1, detail = 0;
    std::atomic<size_t> remaining{0};
};

enum Bucket { init, score, merge, select, adjacency, dedup, finish, bucket_count };
using Inventory = std::array<size_t, bucket_count>;

class Recorder {
public:
    explicit Recorder(Observation& options, Inventory prefix = {}) : options(options), calls(prefix) {}

    bool take(Bucket bucket) {
        return calls[bucket]++ % options.stride == 0;
    }

    void event(const char* name, const std::vector<uint32_t>& ids = {},
               const Bytes& value = {}, size_t count = 0, const operators::Work& work = {}) {
        if (options.mode != Mode::static_capture)
            return;
        const auto hash = content::sha256(value);
        std::ostringstream line;
        line << "[\"" << name << "\",[";
        for (size_t i = 0; i < ids.size(); ++i)
            line << (i ? "," : "") << ids[i];
        line << "]," << count << ',' << value.size() << ",\"" << hash << "\",";
        write_work(line, work);
        line << "]\n";
        append(line.str());
    }

    uint64_t tick(bool enabled) const {
        return enabled ? clock_ns(CLOCK_THREAD_CPUTIME_ID) : 0;
    }

    void sample(const char* bucket, size_t op, int64_t chunk, size_t segment,
                uint64_t start, uint64_t end, const operators::Work& work = {},
                size_t a = 0, size_t b = 0) {
        if (start == 0)
            return;
        std::ostringstream line;
        line << "[\"" << bucket << "\"," << op << ',' << chunk << ',' << segment
             << ',' << start << ',' << end << ',' << a << ',' << b << ',';
        write_work(line, work);
        line << "]\n";
        append(line.str());
    }

    void flush(size_t query) const {
        std::ofstream out(options.directory + "/" + std::to_string(query), std::ios::binary);
        out.write(buffer.data(), buffer.size());
        out.close();
        if (!out)
            throw std::runtime_error("could not flush native observation shard");
    }

private:
    static void write_work(std::ostream& stream, const operators::Work& work) {
        stream << '[';
        for (size_t i = 0; i < work_fields.size(); ++i)
            stream << (i ? "," : "") << work.*work_fields[i];
        stream << ']';
    }

    void append(const std::string& line) {
        auto remaining = options.remaining.load(std::memory_order_relaxed);
        do {
            if (line.size() > remaining || line.size() > options.buffer_limit - buffer.size())
                throw std::runtime_error("native observation output or query buffer budget exceeded");
        } while (!options.remaining.compare_exchange_weak(remaining, remaining - line.size(),
                                                          std::memory_order_relaxed));
        buffer += line;
    }

    Observation& options;
    Inventory calls;
    std::string buffer;
};
} // namespace ndpsim::search::native
