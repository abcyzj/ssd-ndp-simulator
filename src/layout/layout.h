#pragma once
#include "ndpsim/capture.h"
#include <map>
#include <stdexcept>

namespace ndpsim::layout {
struct Error : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct Limits {
    uint64_t file_bytes = 16 * 1024 * 1024, objects = 100000, extents = 200000;
    uint64_t accesses = 100000, plan_sectors = 1000000;
};
struct Slice {
    size_t access_index;
    uint64_t object_offset_bytes, request_offset_bytes, length_bytes;
};
struct Read {
    uint64_t lba, sectors;
    std::vector<Slice> slices;
};
struct Plan {
    uint64_t logical_bytes = 0, sector_bytes = 0;
    std::vector<Read> reads;
};
// Immutable metadata only. One namespace; no device, content store, payload or clock.
class Layout {
public:
    Layout(const std::filesystem::path& reference,
           const std::filesystem::path& declaring_file,
           const std::vector<capture::Object>& objects,
           Limits = {});
    Plan resolve(const std::vector<capture::Access>&) const;
    uint64_t capacity_sectors() const {
        return capacity_;
    }
    uint64_t sector_bytes() const {
        return sector_;
    }
    uint64_t page_bytes() const {
        return page_;
    }
    const std::filesystem::path& source_path() const {
        return path_;
    }
    const std::string& source_bytes() const {
        return source_;
    }
    const std::string& source_sha256() const {
        return hash_;
    }
    const std::string& effective_json() const {
        return effective_;
    }

private:
    struct Extent {
        uint64_t logical, physical, length;
    };
    struct Object {
        uint64_t size;
        std::vector<Extent> extents;
    };
    std::map<std::pair<std::string, uint64_t>, Object> objects_;
    Limits limits_;
    uint64_t capacity_ = 0, sector_ = 0, page_ = 0;
    std::filesystem::path path_;
    std::string source_, hash_, effective_;
};
} // namespace ndpsim::layout
