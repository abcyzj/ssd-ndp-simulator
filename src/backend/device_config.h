#pragma once
#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace ndpsim {
namespace layout {
class Layout;
}
struct DeviceLimits {
    uint64_t xml_bytes = 1024 * 1024;
    uint64_t metadata_bytes = uint64_t(4) * 1024 * 1024 * 1024;
    uint64_t preload_pages = 1000000;
};
struct DeviceGeometry {
    uint64_t channels, chips, dies, planes, blocks_per_plane, pages_per_block;
    uint64_t page_bytes, page_sectors, raw_sectors, namespace_sectors;
    uint64_t metadata_estimate, cmt_entries, queue_depth;
    double lane_bandwidth;
    unsigned lanes;
};
// Strict single-namespace NVMe/FLASH subset, parsed once without touching MQSim
// globals. All validation precedes activation/device allocation. No payload API.
class DeviceConfig {
public:
    DeviceConfig(const std::filesystem::path& reference,
                 const std::filesystem::path& absolute_declaring_file,
                 DeviceLimits = {});
    const DeviceGeometry& geometry() const;
    const std::filesystem::path& source_path() const;
    const std::string& source_bytes() const;
    const std::string& source_sha256() const;
    const std::string& effective_json() const;
    void
    validate_layout(uint64_t capacity_sectors, uint64_t page_bytes, uint64_t preload_sectors) const;
    void validate_layout(const layout::Layout&, uint64_t preload_sectors) const;
    // Installs all supported statics once per process, just before construction.
    // No XML reparsing, implicit defaults, clock advance or device allocation.
    void activate() const;

private:
    struct Data;
    std::shared_ptr<const Data> data_;
};
} // namespace ndpsim
