#include "backend/device_config.h"
#include "layout/layout.h"
#include "exec/Execution_Parameter_Set.h"
#include "ndpsim/capture.h"
#include <nlohmann/json.hpp>
#include <algorithm>
#include <cmath>
#include <fstream>
#include <map>
#include <mutex>
#include <set>

namespace ndpsim {
namespace {
using J = nlohmann::json;
void need(bool ok, const std::string& message) {
    if (!ok)
        throw std::invalid_argument("device config: " + message);
}
uint64_t mul(uint64_t a, uint64_t b) {
    need(!b || a <= UINT64_MAX / b, "geometry multiplication overflow");
    return a * b;
}
uint64_t add(uint64_t a, uint64_t b) {
    need(b <= UINT64_MAX - a, "metadata addition overflow");
    return a + b;
}
bool pow2(uint64_t n) {
    return n && !(n & (n - 1));
}
struct Rule {
    const char* group;
    const char* key;
    const char* fixed;
    uint64_t low, high;
};
// Numeric ranges are in MQSim's XML units: ns, bytes, MT/s, GB/s/lane.
// Fixed values deliberately reject backend modes not covered by this adapter.
const Rule rules[] = {{"host", "PCIe_Lane_Bandwidth", nullptr, 0, 0},
                      {"host", "PCIe_Lane_Count", nullptr, 1, 16},
                      {"host", "SATA_Processing_Delay", nullptr, 0, 1000000000},
                      {"host", "Enable_ResponseTime_Logging", "false", 0, 0},
                      {"host", "ResponseTime_Logging_Period_Length", nullptr, 1, 1000000000},
                      {"device", "Seed", nullptr, 1, 2147483600},
                      {"device", "Enabled_Preconditioning", "false", 0, 0},
                      {"device", "Memory_Type", "FLASH", 0, 0},
                      {"device", "HostInterface_Type", "NVME", 0, 0},
                      {"device", "IO_Queue_Depth", nullptr, 2, 1024},
                      {"device", "Queue_Fetch_Size", nullptr, 1, 1023},
                      {"device", "Caching_Mechanism", "SIMPLE", 0, 0},
                      {"device", "Data_Cache_Sharing_Mode", "SHARED", 0, 0},
                      {"device", "Data_Cache_Capacity", nullptr, 4096, 1073741824},
                      {"device", "Data_Cache_DRAM_Row_Size", nullptr, 512, 65536},
                      {"device", "Data_Cache_DRAM_Data_Rate", nullptr, 1, 10000},
                      {"device", "Data_Cache_DRAM_Data_Busrt_Size", nullptr, 1, 1024},
                      {"device", "Data_Cache_DRAM_tRCD", nullptr, 1, 1000000000},
                      {"device", "Data_Cache_DRAM_tCL", nullptr, 1, 1000000000},
                      {"device", "Data_Cache_DRAM_tRP", nullptr, 1, 1000000000},
                      {"device", "Address_Mapping", "PAGE_LEVEL", 0, 0},
                      {"device", "Ideal_Mapping_Table", "false", 0, 0},
                      {"device", "CMT_Capacity", nullptr, 128, 1073741824},
                      {"device", "CMT_Sharing_Mode", "SHARED", 0, 0},
                      {"device", "Plane_Allocation_Scheme", "CWDP", 0, 0},
                      {"device", "Transaction_Scheduling_Policy", "PRIORITY_OUT_OF_ORDER", 0, 0},
                      {"device", "Overprovisioning_Ratio", nullptr, 0, 0},
                      {"device", "GC_Exec_Threshold", "0.05000", 0, 0},
                      {"device", "GC_Block_Selection_Policy", "RGA", 0, 0},
                      {"device", "Use_Copyback_for_GC", "false", 0, 0},
                      {"device", "Preemptible_GC_Enabled", "false", 0, 0},
                      {"device", "GC_Hard_Threshold", "0.005000", 0, 0},
                      {"device", "Dynamic_Wearleveling_Enabled", "true", 0, 0},
                      {"device", "Static_Wearleveling_Enabled", "true", 0, 0},
                      {"device", "Static_Wearleveling_Threshold", nullptr, 1, 1000000},
                      {"device", "Preferred_suspend_erase_time_for_read", nullptr, 1, 1000000000},
                      {"device", "Preferred_suspend_erase_time_for_write", nullptr, 1, 1000000000},
                      {"device", "Preferred_suspend_write_time_for_read", nullptr, 1, 1000000000},
                      {"device", "Flash_Channel_Count", nullptr, 1, 16},
                      {"device", "Flash_Channel_Width", nullptr, 1, 16},
                      {"device", "Channel_Transfer_Rate", nullptr, 1, 1000},
                      {"device", "Chip_No_Per_Channel", nullptr, 1, 8},
                      {"device", "Flash_Comm_Protocol", "NVDDR2", 0, 0},
                      {"flash", "Flash_Technology", "MLC", 0, 0},
                      {"flash", "CMD_Suspension_Support", "ERASE", 0, 0},
                      {"flash", "Page_Read_Latency_LSB", nullptr, 1, 1000000000},
                      {"flash", "Page_Read_Latency_CSB", nullptr, 1, 1000000000},
                      {"flash", "Page_Read_Latency_MSB", nullptr, 1, 1000000000},
                      {"flash", "Page_Program_Latency_LSB", nullptr, 1, 1000000000},
                      {"flash", "Page_Program_Latency_CSB", nullptr, 1, 1000000000},
                      {"flash", "Page_Program_Latency_MSB", nullptr, 1, 1000000000},
                      {"flash", "Block_Erase_Latency", nullptr, 1, 1000000000},
                      {"flash", "Block_PE_Cycles_Limit", nullptr, 1, 10000000},
                      {"flash", "Suspend_Erase_Time", nullptr, 1, 1000000000},
                      {"flash", "Suspend_Program_Time", nullptr, 1, 1000000000},
                      {"flash", "Die_No_Per_Chip", nullptr, 1, 8},
                      {"flash", "Plane_No_Per_Die", nullptr, 1, 4},
                      {"flash", "Block_No_Per_Plane", nullptr, 64, 65536},
                      {"flash", "Page_No_Per_Block", nullptr, 16, 256},
                      {"flash", "Page_Capacity", nullptr, 512, 16384},
                      {"flash", "Page_Metadat_Capacity", nullptr, 1, 4096}};
void container(rapidxml::xml_node<>* n) {
    need(n && !n->first_attribute(), "missing container or XML attributes unsupported");
    auto value = std::string(n->value(), n->value_size());
    need(value.find_first_not_of(" \r\n\t") == std::string::npos, "mixed XML content unsupported");
}
} // namespace
struct DeviceConfig::Data {
    std::filesystem::path path;
    std::string source, hash, effective;
    J values;
    DeviceGeometry g{};
    DeviceLimits limits;
};
DeviceConfig::DeviceConfig(const std::filesystem::path& reference,
                           const std::filesystem::path& declaring,
                           DeviceLimits limits) {
    need(declaring.is_absolute(), "declaring file must be absolute (no cwd fallback)");
    need(!reference.empty(), "empty XML reference");
    auto d = std::make_shared<Data>();
    d->limits = limits;
    d->path = std::filesystem::canonical(
        reference.is_absolute() ? reference : declaring.parent_path() / reference);
    need(std::filesystem::file_size(d->path) <= limits.xml_bytes, "XML byte budget");
    std::ifstream file(d->path, std::ios::binary);
    need(bool(file), "cannot open XML");
    char block[8192];
    while (file.read(block, sizeof block) || file.gcount()) {
        need(uint64_t(file.gcount()) <= limits.xml_bytes - d->source.size(), "XML byte budget");
        d->source.append(block, size_t(file.gcount()));
    }
    need(file.eof(), "XML read failure");
    // No entities/DTD/PI/comments, embedded NUL or hidden trailing roots. MQSim
    // accepts a very loose XML dialect; this adapter deliberately narrows it.
    need(d->source.find('\0') == std::string::npos && d->source.find("<!") == std::string::npos &&
             d->source.find('&') == std::string::npos,
         "XML entity/DTD/comment/NUL unsupported");
    unsigned depth = 0;
    for (size_t i = 0; i < d->source.size(); ++i)
        if (d->source[i] == '<') {
            if (i + 1 < d->source.size() && d->source[i + 1] == '/') {
                need(depth > 0, "XML nesting");
                --depth;
            } else if (i + 1 < d->source.size() && d->source[i + 1] != '?')
                need(++depth <= 4, "XML nesting limit");
        }
    std::vector<char> text(d->source.begin(), d->source.end());
    text.push_back(0);
    rapidxml::xml_document<> doc;
    doc.parse<rapidxml::parse_full>(text.data());
    auto* root = doc.first_node();
    if (root && root->type() == rapidxml::node_declaration) {
        for (auto* a = root->first_attribute(); a; a = a->next_attribute())
            need(std::string(a->name()) == "version" || std::string(a->name()) == "encoding",
                 "XML declaration attribute");
        root = root->next_sibling();
    }
    need(root && root->type() == rapidxml::node_element &&
             std::string(root->name()) == "Execution_Parameter_Set" && !root->next_sibling(),
         "expected single Execution_Parameter_Set");
    container(root);
    std::map<std::string, rapidxml::xml_node<>*> groups;
    for (auto* n = root->first_node(); n; n = n->next_sibling()) {
        auto key = std::string(n->name());
        need((key == "Host_Parameter_Set" || key == "Device_Parameter_Set") &&
                 groups.emplace(key, n).second,
             "unknown/duplicate XML group");
        container(n);
    }
    need(groups.size() == 2, "missing XML group");
    std::map<std::string, std::map<std::string, std::string>> raw;
    auto leaves = [&](rapidxml::xml_node<>* parent, const std::string& group) {
        for (auto* n = parent->first_node(); n; n = n->next_sibling()) {
            if (group == "device" && std::string(n->name()) == "Flash_Parameter_Set") {
                need(groups.emplace("Flash_Parameter_Set", n).second,
                     "duplicate Flash_Parameter_Set");
                container(n);
                continue;
            }
            need(n->type() == rapidxml::node_element && !n->first_attribute(),
                 "XML leaf/attribute unsupported");
            // parse_full emits a data node as the sole child of a scalar leaf.
            auto* child = n->first_node();
            need(!child || (child->type() == rapidxml::node_data && !child->next_sibling()),
                 "nested scalar");
            need(raw[group].emplace(n->name(), n->value()).second, "duplicate XML field");
        }
    };
    leaves(groups.at("Host_Parameter_Set"), "host");
    leaves(groups.at("Device_Parameter_Set"), "device");
    need(groups.count("Flash_Parameter_Set"), "missing flash group");
    leaves(groups.at("Flash_Parameter_Set"), "flash");
    size_t expected = 0, actual = 0;
    for (const auto& group : raw)
        actual += group.second.size();
    for (const auto& r : rules) {
        ++expected;
        need(raw[r.group].count(r.key), std::string("missing field ") + r.key);
        auto s = raw[r.group].at(r.key);
        if (r.fixed) {
            need(s == r.fixed, std::string("Unsupported value for ") + r.key);
            d->values[r.group][r.key] = s;
        } else if (!r.high) {
            need(!s.empty() && s.find_first_not_of("0123456789.") == std::string::npos,
                 "invalid decimal");
            size_t used = 0;
            double v = std::stod(s, &used);
            need(used == s.size() && std::isfinite(v), "invalid finite decimal");
            if (std::string(r.key) == "PCIe_Lane_Bandwidth")
                need(v >= 0.001 && v <= 64, "PCIe lane GB/s range");
            else
                need(v >= 0.05 && v <= 0.30, "overprovisioning ratio range");
            d->values[r.group][r.key] = v;
        } else {
            need(!s.empty() && s.find_first_not_of("0123456789") == std::string::npos,
                 "expected unsigned decimal integer");
            size_t used = 0;
            auto v = std::stoull(s, &used);
            need(used == s.size() && v >= r.low && v <= r.high, std::string("range for ") + r.key);
            d->values[r.group][r.key] = v;
        }
    }
    need(expected == actual, "unknown XML field");
    auto u = [&](const char* group, const char* key) {
        return d->values.at(group).at(key).get<uint64_t>();
    };
    auto& g = d->g;
    g.channels = u("device", "Flash_Channel_Count");
    g.chips = u("device", "Chip_No_Per_Channel");
    g.dies = u("flash", "Die_No_Per_Chip");
    g.planes = u("flash", "Plane_No_Per_Die");
    g.blocks_per_plane = u("flash", "Block_No_Per_Plane");
    g.pages_per_block = u("flash", "Page_No_Per_Block");
    g.page_bytes = u("flash", "Page_Capacity");
    for (auto n : {g.channels,
                   g.chips,
                   g.dies,
                   g.planes,
                   g.blocks_per_plane,
                   g.pages_per_block,
                   g.page_bytes})
        need(pow2(n), "power of two geometry required");
    need(u("flash", "Page_Metadat_Capacity") <= g.page_bytes,
         "page metadata exceeds page capacity");
    need(pow2(u("device", "Flash_Channel_Width")), "channel width must be power of two");
    need(pow2(u("device", "Data_Cache_DRAM_Row_Size")) &&
             pow2(u("device", "Data_Cache_DRAM_Data_Busrt_Size")) &&
             u("device", "Data_Cache_DRAM_Data_Busrt_Size") <=
                 u("device", "Data_Cache_DRAM_Row_Size"),
         "DRAM row/burst geometry");
    need(u("device", "Data_Cache_Capacity") >= g.page_bytes &&
             u("device", "Data_Cache_Capacity") % g.page_bytes == 0,
         "data cache capacity must contain whole pages");
    g.page_sectors = g.page_bytes / 512;
    auto planes = mul(mul(g.channels, g.chips), mul(g.dies, g.planes));
    auto blocks = mul(planes, g.blocks_per_plane);
    auto pages = mul(blocks, g.pages_per_block);
    g.raw_sectors = mul(pages, g.page_sectors);
    // MQSim has intermediate unsigned-int products even though LBA is uint64.
    need(g.raw_sectors <= UINT32_MAX, "Unsupported MQSim uint32 geometry products");
    const auto op = d->values["device"]["Overprovisioning_Ratio"].get<double>();
    g.namespace_sectors =
        mul(planes,
            uint64_t(double(g.blocks_per_plane * g.pages_per_block * g.page_sectors) * (1.0 - op)));
    auto entry = uint64_t(std::ceil((2 * std::log2(double(pages)) + g.page_sectors) / 8));
    g.cmt_entries = u("device", "CMT_Capacity") / entry;
    need(g.cmt_entries >= 2, "CMT too small for mapping");
    g.metadata_estimate =
        add(add(mul(pages, 256), mul(blocks, 4096)),
            add(mul(u("device", "CMT_Capacity"), 64), u("device", "Data_Cache_Capacity")));
    need(g.metadata_estimate <= limits.metadata_bytes,
         "metadata estimate exceeds allocation budget");
    g.queue_depth = u("device", "IO_Queue_Depth");
    need(u("device", "Queue_Fetch_Size") < g.queue_depth, "queue fetch must be below queue depth");
    g.lane_bandwidth = d->values["host"]["PCIe_Lane_Bandwidth"].get<double>();
    g.lanes = unsigned(u("host", "PCIe_Lane_Count"));
    J partition = {{"priority", "HIGH"},
                   {"data_cache", "TURNED_OFF"},
                   {"start_lba", 0},
                   {"capacity_sectors", g.namespace_sectors}};
    for (auto row : std::vector<std::pair<std::string, uint64_t>>{{"channels", g.channels},
                                                                  {"chips", g.chips},
                                                                  {"dies", g.dies},
                                                                  {"planes", g.planes}}) {
        partition[row.first] = J::array();
        for (uint64_t i = 0; i < row.second; ++i)
            partition[row.first].push_back(i);
    }
    d->hash = capture::sha256(d->source);
    d->effective = J{{"format", "ndpsim-device-effective/1"},
                     {"source_path", d->path.string()},
                     {"source_sha256", d->hash},
                     {"xml_fields", d->values},
                     {"partition", partition},
                     {"raw_sectors", g.raw_sectors},
                     {"page_bytes", g.page_bytes},
                     {"metadata_estimate_bytes", g.metadata_estimate},
                     {"cmt_entries", g.cmt_entries},
                     {"initial_state", "explicit-full-page-preload/drained/CMT-retained"},
                     {"parameter_source", "configured_unvalidated"},
                     {"limits",
                      {{"xml_bytes", limits.xml_bytes},
                       {"metadata_bytes", limits.metadata_bytes},
                       {"preload_pages", limits.preload_pages}}}}
                       .dump();
    data_ = std::move(d);
}
const DeviceGeometry& DeviceConfig::geometry() const {
    return data_->g;
}
const std::filesystem::path& DeviceConfig::source_path() const {
    return data_->path;
}
const std::string& DeviceConfig::source_bytes() const {
    return data_->source;
}
const std::string& DeviceConfig::source_sha256() const {
    return data_->hash;
}
const std::string& DeviceConfig::effective_json() const {
    return data_->effective;
}
void DeviceConfig::validate_layout(uint64_t capacity, uint64_t page, uint64_t preload) const {
    auto g = geometry();
    need(page == g.page_bytes, "Layout page geometry mismatch");
    need(capacity > 0 && capacity <= g.namespace_sectors, "layout exceeds actual MQSim namespace");
    need(preload > 0 && preload % g.page_sectors == 0 && preload <= g.namespace_sectors,
         "preload capacity/page alignment");
    need(preload / g.page_sectors <= data_->limits.preload_pages, "preload page budget");
}
void DeviceConfig::validate_layout(const layout::Layout& layout, uint64_t preload) const {
    validate_layout(layout.capacity_sectors(), layout.page_bytes(), preload);
    need(layout.mapped_end_sectors() <= preload, "preload does not cover mapped Layout extents");
}
void DeviceConfig::activate() const {
    static bool claimed = false;
    need(!claimed, "only one MQSim device lifetime per process is supported");
    claimed = true;
    const auto& v = data_->values;
    auto u = [&](const char* group, const char* key) {
        return v.at(group).at(key).get<uint64_t>();
    };
    auto real = [&](const char* group, const char* key) {
        return v.at(group).at(key).get<double>();
    };
#define H(k) Host_Parameter_Set::k = u("host", #k)
#define D(k) Device_Parameter_Set::k = u("device", #k)
#define F(k) Flash_Parameter_Set::k = u("flash", #k)
    Host_Parameter_Set::PCIe_Lane_Bandwidth = real("host", "PCIe_Lane_Bandwidth");
    H(PCIe_Lane_Count);
    H(SATA_Processing_Delay);
    H(ResponseTime_Logging_Period_Length);
    Host_Parameter_Set::Enable_ResponseTime_Logging = false;
    D(Seed);
    D(IO_Queue_Depth);
    D(Queue_Fetch_Size);
    D(Data_Cache_Capacity);
    D(Data_Cache_DRAM_Row_Size);
    D(Data_Cache_DRAM_Data_Rate);
    D(Data_Cache_DRAM_Data_Busrt_Size);
    D(Data_Cache_DRAM_tRCD);
    D(Data_Cache_DRAM_tCL);
    D(Data_Cache_DRAM_tRP);
    D(CMT_Capacity);
    D(Static_Wearleveling_Threshold);
    D(Preferred_suspend_erase_time_for_read);
    D(Preferred_suspend_erase_time_for_write);
    D(Preferred_suspend_write_time_for_read);
    D(Flash_Channel_Count);
    D(Flash_Channel_Width);
    D(Channel_Transfer_Rate);
    D(Chip_No_Per_Channel);
    Device_Parameter_Set::Enabled_Preconditioning = false;
    Device_Parameter_Set::Memory_Type = NVM::NVM_Type::FLASH;
    Device_Parameter_Set::HostInterface_Type = HostInterface_Types::NVME;
    Device_Parameter_Set::Caching_Mechanism = SSD_Components::Caching_Mechanism::SIMPLE;
    Device_Parameter_Set::Data_Cache_Sharing_Mode = SSD_Components::Cache_Sharing_Mode::SHARED;
    Device_Parameter_Set::Address_Mapping = SSD_Components::Flash_Address_Mapping_Type::PAGE_LEVEL;
    Device_Parameter_Set::Ideal_Mapping_Table = false;
    Device_Parameter_Set::CMT_Sharing_Mode = SSD_Components::CMT_Sharing_Mode::SHARED;
    Device_Parameter_Set::Plane_Allocation_Scheme =
        SSD_Components::Flash_Plane_Allocation_Scheme_Type::CWDP;
    Device_Parameter_Set::Transaction_Scheduling_Policy =
        SSD_Components::Flash_Scheduling_Type::PRIORITY_OUT_OF_ORDER;
    Device_Parameter_Set::Overprovisioning_Ratio = real("device", "Overprovisioning_Ratio");
    Device_Parameter_Set::GC_Exec_Threshold = 0.05;
    Device_Parameter_Set::GC_Hard_Threshold = 0.005;
    Device_Parameter_Set::GC_Block_Selection_Policy =
        SSD_Components::GC_Block_Selection_Policy_Type::RGA;
    Device_Parameter_Set::Use_Copyback_for_GC = false;
    Device_Parameter_Set::Preemptible_GC_Enabled = false;
    Device_Parameter_Set::Dynamic_Wearleveling_Enabled = true;
    Device_Parameter_Set::Static_Wearleveling_Enabled = true;
    Device_Parameter_Set::Flash_Comm_Protocol = SSD_Components::ONFI_Protocol::NVDDR2;
    Flash_Parameter_Set::Flash_Technology = Flash_Technology_Type::MLC;
    Flash_Parameter_Set::CMD_Suspension_Support = NVM::FlashMemory::Command_Suspension_Mode::ERASE;
    F(Page_Read_Latency_LSB);
    F(Page_Read_Latency_CSB);
    F(Page_Read_Latency_MSB);
    F(Page_Program_Latency_LSB);
    F(Page_Program_Latency_CSB);
    F(Page_Program_Latency_MSB);
    F(Block_Erase_Latency);
    F(Block_PE_Cycles_Limit);
    F(Suspend_Erase_Time);
    F(Suspend_Program_Time);
    F(Die_No_Per_Chip);
    F(Plane_No_Per_Die);
    F(Block_No_Per_Plane);
    F(Page_No_Per_Block);
    F(Page_Capacity);
    F(Page_Metadat_Capacity);
#undef H
#undef D
#undef F
}
} // namespace ndpsim
