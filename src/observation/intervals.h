#pragma once
#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <vector>
#include <type_traits>
namespace ndpsim {
// Bounded-memory external merge sort. Runs compact by binary carry; callbacks may
// arrive in any order. Temporary files fail explicitly on disk exhaustion.
template <class T> class DiskRecords {
    static_assert(std::is_trivially_copyable<T>::value, "disk record must be POD");
    struct Close {
        void operator()(FILE* f) const {
            if (f)
                std::fclose(f);
        }
    };

public:
    using File = std::unique_ptr<FILE, Close>;
    static File file() {
        File f(std::tmpfile());
        if (!f)
            throw std::runtime_error("observation temporary file unavailable");
        return f;
    }
    static void rewind(FILE* f) {
        if (std::fflush(f) || std::fseek(f, 0, SEEK_SET))
            throw std::runtime_error("observation flush/seek failed");
    }
    static void write(FILE* f, const T& v) {
        if (std::fwrite(&v, sizeof(v), 1, f) != 1)
            throw std::runtime_error("observation disk budget exhausted/write failed");
    }
    static bool read(FILE* f, T& v) {
        auto n = std::fread(&v, 1, sizeof(v), f);
        if (std::ferror(f) || (n && n != sizeof(v)))
            throw std::runtime_error("observation temporary read failed");
        return n != 0;
    }
    // Raw history capped at 1 GiB; sort scratch uses additional disk, not RAM.
    void push_back(const T& v) {
        if (bytes_ > (uint64_t(1) << 30) - sizeof(T))
            throw std::runtime_error("observation raw spool budget exhausted (1 GiB)");
        write(raw_.get(), v);
        bytes_ += sizeof(T);
    }
    template <class F> void each(F emit) {
        if (std::fflush(raw_.get()))
            throw std::runtime_error("observation flush failed");
        rewind(raw_.get());
        T v;
        while (read(raw_.get(), v))
            emit(v);
        if (std::fseek(raw_.get(), 0, SEEK_END))
            throw std::runtime_error("observation seek failed");
    }
    void clear() {
        raw_ = file();
        bytes_ = 0;
    }
    template <class F> void sorted(F emit) {
        std::array<File, 64> runs;
        std::vector<T> chunk;
        chunk.reserve(4096);
        auto merge = [](File a, File b) {
            auto out = file();
            rewind(a.get());
            rewind(b.get());
            T x, y;
            bool ax = read(a.get(), x), by = read(b.get(), y);
            while (ax || by) {
                if (ax && (!by || x < y)) {
                    write(out.get(), x);
                    ax = read(a.get(), x);
                } else {
                    write(out.get(), y);
                    by = read(b.get(), y);
                }
            }
            return out;
        };
        auto flush = [&] {
            if (chunk.empty())
                return;
            std::sort(chunk.begin(), chunk.end());
            auto run = file();
            for (auto& v : chunk)
                write(run.get(), v);
            chunk.clear();
            size_t i = 0;
            while (i < runs.size() && runs[i]) {
                run = merge(std::move(run), std::move(runs[i]));
                ++i;
            }
            if (i == runs.size())
                throw std::overflow_error("observation run count");
            runs[i] = std::move(run);
        };
        each([&](const T& v) {
            chunk.push_back(v);
            if (chunk.size() == 4096)
                flush();
        });
        flush();
        File all;
        for (auto& run : runs)
            if (run)
                all = all ? merge(std::move(all), std::move(run)) : std::move(run);
        if (all) {
            rewind(all.get());
            T v;
            while (read(all.get(), v))
                emit(v);
        }
    }

private:
    uint64_t bytes_ = 0;
    File raw_ = file();
};
} // namespace ndpsim
