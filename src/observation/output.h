#pragma once
#include <fstream>
#include <stdexcept>
namespace ndpsim {
struct OutputBudget {
    uint64_t limit = uint64_t(2) << 30, used = 0;
};
// Shared budget for required records and optional detail. Exhaustion is a failed
// experiment; no sampling, truncation, or dropping final results.
class BudgetFile : public std::ostream {
    class Buffer : public std::streambuf {
        OutputBudget& budget_;

    public:
        std::filebuf file;
        explicit Buffer(OutputBudget& b) : budget_(b) {
        }
        std::streamsize xsputn(const char* p, std::streamsize n) override {
            if (uint64_t(n) > budget_.limit - budget_.used)
                throw std::runtime_error("experiment output byte budget exhausted");
            auto written = file.sputn(p, n);
            budget_.used += written;
            return written;
        }
        int_type overflow(int_type c) override {
            if (traits_type::eq_int_type(c, traits_type::eof()))
                return traits_type::not_eof(c);
            char v = traits_type::to_char_type(c);
            return xsputn(&v, 1) == 1 ? c : traits_type::eof();
        }
        int sync() override {
            return file.pubsync();
        }
    } buffer_;

public:
    explicit BudgetFile(OutputBudget& b) : std::ostream(nullptr), buffer_(b) {
        rdbuf(&buffer_);
        exceptions(std::ios::badbit);
    }
    void open(const std::string& path) {
        if (!buffer_.file.open(path, std::ios::out | std::ios::trunc))
            setstate(std::ios::failbit);
    }
    bool is_open() const {
        return buffer_.file.is_open();
    }
};
} // namespace ndpsim
