#pragma once
#include "observation/intervals.h"
#include <cstring>
#include "sim/Observation.h"
#include <algorithm>
#include <map>
#include <tuple>
#include <vector>
#include <cstdio>
#include <stdexcept>
#include <functional>
#include <string>
#include <cstdarg>
namespace ndpsim {
class Observation {
    struct Interval {
        char kind[64];
        uint64_t start, end;
        unsigned channel, chip, die;
    };
    struct Span {
        uint64_t first, second;
        bool operator<(const Span& rhs) const {
            return std::tie(first, second) < std::tie(rhs.first, rhs.second);
        }
    };
    DiskRecords<Interval> intervals_;
    void interval(const char* kind, uint64_t a, uint64_t b, unsigned c, unsigned h, unsigned d) {
        Interval i{};
        if (std::strlen(kind) >= sizeof(i.kind))
            throw std::invalid_argument("observation kind too long");
        std::strcpy(i.kind, kind);
        i.start = a;
        i.end = b;
        i.channel = c;
        i.chip = h;
        i.die = d;
        intervals_.push_back(i);
    }
    std::map<std::tuple<unsigned, unsigned, unsigned>, uint64_t> ready_;
    uint64_t sequence_ = 0;
    bool recording_ = true;
    void output(bool stat, const char* format, ...) {
        va_list args;
        va_start(args, format);
        va_list copy;
        va_copy(copy, args);
        auto n = std::vsnprintf(nullptr, 0, format, copy);
        va_end(copy);
        std::vector<char> text(n + 1);
        std::vsnprintf(text.data(), text.size(), format, args);
        va_end(args);
        if (emit) {
            std::string v(text.data());
            auto start = v.find('{');
            v = v.substr(start);
            if (!v.empty() && v.back() == '\n')
                v.pop_back();
            emit(stat, v);
        } else
            std::printf("%s", text.data());
    }

public:
    std::function<void(bool, const std::string&)> emit;
    explicit Observation(bool recording = true) : recording_(recording) {
        if (MQSimObservation::sink)
            throw std::logic_error("observer already attached");
        MQSimObservation::sink = [this](const char* kind,
                                        uint64_t start,
                                        uint64_t end,
                                        unsigned c,
                                        unsigned h,
                                        unsigned d) {
            if (end < start)
                throw std::logic_error("negative observation interval");
            if (!recording_)
                return;
            auto key = std::make_tuple(c, h, d);
            if (std::string(kind) == "array")
                ready_[key] = end;
            if (std::string(kind) == "data_out_bus") {
                auto it = ready_.find(key);
                if (it != ready_.end()) {
                    // Subsequent data-out of a multi-plane command waits after the previous transfer.
                    if (start < it->second)
                        throw std::logic_error("data out before array ready");
                    interval("wait_data_out", it->second, start, c, h, d);
                    it->second = end;
                }
            }
            interval(kind, start, end, c, h, d);
        };
    }
    ~Observation() {
        MQSimObservation::sink = {};
    }
    void request(const char* kind, uint64_t id, uint64_t time, uint64_t lba, unsigned sectors) {
        output(false,
               "OBS "
               "{\"sequence\":%llu,\"event_kind\":\"%s\",\"time_ns\":%llu,\"device\":0,\"request_"
               "id\":%llu,\"lba\":%llu,\"bytes\":%u,\"byte_basis\":\"sector\",\"source\":"
               "\"external_flow\",\"query_id\":null,\"task_id\":null,\"null_reason\":\"storage "
               "fixture has no algorithm IDs\"}\n",
               ++sequence_,
               kind,
               time,
               id,
               lba,
               sectors * 512);
    }
    void reset_window() {
        recording_ = true;
        intervals_.clear();
        ready_.clear();
    }
    void report(uint64_t begin, uint64_t end) {
        if (end < begin)
            throw std::invalid_argument("negative observation window");
        using Key = std::tuple<std::string, unsigned, int, int>;
        std::map<Key, DiskRecords<Span>> groups;
        intervals_.each([&](const auto& i) {
            auto a = std::max(begin, i.start), b = std::min(end, i.end);
            if (b < a || i.end < begin || i.start > end)
                return;
            output(
                false,
                "OBS "
                "{\"sequence\":%llu,\"event_kind\":\"interval\",\"kind\":\"%s\",\"time_ns\":%llu,"
                "\"start_ns\":%llu,\"end_ns\":%llu,\"device\":0,\"channel\":%u,\"chip\":%u,\"die\":"
                "%u,\"source\":\"MQSim\",\"request_id\":null,\"transaction_ids\":null,\"null_"
                "reason\":\"chip command hook has no request or transaction identity\"}\n",
                ++sequence_,
                i.kind,
                b,
                a,
                b,
                i.channel,
                i.chip,
                i.die);
            groups[{i.kind, i.channel, i.chip, i.die}].push_back({a, b});
            if (std::string(i.kind).find("bus") != std::string::npos)
                groups[{"bus", i.channel, -1, -1}].push_back({a, b});
        });
        for (auto& g : groups) {
            auto& v = g.second;
            uint64_t total = 0, a = 0, b = 0;
            v.sorted([&](auto x) {
                if (x.first > b) {
                    total += b - a;
                    a = x.first;
                    b = x.second;
                } else
                    b = std::max(b, x.second);
            });
            total += b - a;
            auto [kind, c, h, d] = g.first;
            output(true,
                   "STAT "
                   "{\"kind\":\"%s\",\"channel\":%u,\"chip\":%d,\"die\":%d,\"union_ns\":%llu,"
                   "\"window_start_ns\":%llu,\"window_end_ns\":%llu}\n",
                   kind.c_str(),
                   c,
                   h,
                   d,
                   total,
                   begin,
                   end);
        }
    }
};
} // namespace ndpsim
