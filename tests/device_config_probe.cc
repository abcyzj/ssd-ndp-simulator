#include "backend/device_config.h"
#include "backend/ordinary_device.h"
#include "backend/lifecycle.h"
#include "layout/layout.h"
#include "ssd/FTL.h"
#include "ssd/Address_Mapping_Unit_Page_Level.h"
#include "ssd/Stats.h"
#include <nlohmann/json.hpp>
#include <fstream>
#include <iostream>
using J = nlohmann::json;
int main(int argc, char** argv) {
    try {
        if (argc != 4)
            throw std::invalid_argument("usage: probe XML declaring-file request.json");
        std::ifstream f(argv[3]);
        J req;
        f >> req;
        ndpsim::DeviceLimits limits;
        limits.metadata_bytes = req.value("metadata_bytes", limits.metadata_bytes);
        limits.xml_bytes = req.value("xml_bytes", limits.xml_bytes);
        limits.preload_pages = req.value("preload_pages", limits.preload_pages);
        const auto old_seed = Device_Parameter_Set::Seed;
        ndpsim::DeviceConfig config(argv[1], argv[2], limits);
        if (Device_Parameter_Set::Seed != old_seed || Simulator->GetObject("SSDDevice"))
            throw std::logic_error("load mutated backend");
        J out = {{"effective", J::parse(config.effective_json())},
                 {"source_bytes", config.source_bytes()},
                 {"allocated", false}};
        auto g = config.geometry();
        uint64_t preload = req.value("preload_sectors", 512ULL);
        std::unique_ptr<ndpsim::layout::Layout> layout;
        if (req.contains("layout")) {
            std::vector<ndpsim::capture::Object> objects;
            for (const auto& o : req.at("objects")) {
                ndpsim::capture::Object x;
                x.id = o.at("id");
                x.version = o.at("version");
                x.size_bytes = o.at("size_bytes");
                objects.push_back(x);
            }
            layout = std::make_unique<ndpsim::layout::Layout>(
                req.at("layout").get<std::string>(), argv[2], objects);
            config.validate_layout(*layout, preload);
        } else
            config.validate_layout(req.value("capacity_sectors", preload),
                                   req.value("page_bytes", g.page_bytes),
                                   preload);
        if (req.value("validate_only", false)) {
            std::cout << out.dump() << '\n';
            return 0;
        }
        if (req.value("lifecycle", false)) {
            ndpsim::LifecycleBackend backend(config);
            backend.initialize();
            backend.finish();
            out["lifecycle_time_ns"] = backend.time_ns();
            out["registry_empty"] = !Simulator->GetObject("SSDDevice");
            std::cout << out.dump() << '\n';
            return 0;
        }
        if (req.value("remove_source", false))
            std::filesystem::remove(config.source_path());
        std::unique_ptr<ndpsim::OrdinaryDevice> device;
        if (layout)
            device = std::make_unique<ndpsim::OrdinaryDevice>(preload, config, *layout);
        else
            device = std::make_unique<ndpsim::OrdinaryDevice>(preload, config);
        out["allocated"] = true;
        out["actual_namespace_sectors"] = device->namespace_sectors;
        out["host_interface_field"] =
            device->device->Host_interface->Get_max_logical_sector_address();
        out["partition_end_lba"] =
            Utils::Logical_Address_Partitioning_Unit::End_lha_available_to_flow(0);
        out["applied"] = {{"queue_depth", Device_Parameter_Set::IO_Queue_Depth},
                          {"queue_fetch", Device_Parameter_Set::Queue_Fetch_Size},
                          {"lane_bandwidth", Host_Parameter_Set::PCIe_Lane_Bandwidth},
                          {"lanes", Host_Parameter_Set::PCIe_Lane_Count},
                          {"read_lsb_ns", Flash_Parameter_Set::Page_Read_Latency_LSB},
                          {"read_msb_ns", Flash_Parameter_Set::Page_Read_Latency_MSB},
                          {"program_lsb_ns", Flash_Parameter_Set::Page_Program_Latency_LSB},
                          {"cmt_bytes", Device_Parameter_Set::CMT_Capacity}};
        uint64_t writes = 0, reads = 0;
        bool measuring = false;
        out["completions"] = J::array();
        device->flow->completed = [&](ndpsim::ExternalFlow::Completion c) {
            if (!measuring) {
                if (!c.write || c.sectors != g.page_sectors || c.lba != writes * g.page_sectors)
                    throw std::logic_error("preload sequence");
                ++writes;
            } else {
                if (c.write)
                    throw std::logic_error("measurement write");
                ++reads;
                out["completions"].push_back({{"lba", c.lba},
                                              {"sectors", c.sectors},
                                              {"time", c.time - device->window->origin()}});
            }
        };
        device->start();
        uint64_t steps = 0;
        uint64_t misses = 0;
        for (;;) {
            auto next = Simulator->Peek_next_time();
            if (next) {
                if (++steps > 500000)
                    throw std::logic_error("step budget");
                Simulator->Dispatch_current_time(*next);
            }
            if (!measuring &&
                device->window->advance(!Simulator->Peek_next_time(), Simulator->Time())) {
                measuring = true;
                auto* amu = static_cast<SSD_Components::FTL*>(device->device->Firmware)
                                ->Address_Mapping_Unit;
                out["initial"] = {
                    {"origin_ns", device->window->origin()},
                    {"preload_pages", writes},
                    {"cmt_entries", amu->Get_cmt_capacity()},
                    {"preconditioning_entries", amu->Get_current_cmt_occupancy_for_stream(0)},
                    {"pending", device->flow->pending()}};
                out["initial_cache"] =
                    static_cast<SSD_Components::Address_Mapping_Unit_Page_Level*>(amu)
                        ->Snapshot_cmt(0);
                misses = SSD_Components::Stats::readTR_CMT_miss;
                for (const auto& r : req.at("reads"))
                    device->window->read(r.at("lba"), r.at("sectors"));
            }
            if (!Simulator->Peek_next_time())
                break;
        }
        if (!measuring || reads != req.at("reads").size() || device->flow->pending())
            throw std::logic_error("not drained");
        out["read_cmt_misses"] = SSD_Components::Stats::readTR_CMT_miss - misses;
        out["drained"] = true;
        device.reset();
        out["registry_empty"] = !Simulator->GetObject("SSDDevice");
        std::cout << out.dump() << '\n';
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        std::cout
            << J{{"allocated", bool(Simulator->GetObject("SSDDevice"))}, {"error", e.what()}}.dump()
            << '\n';
        return 1;
    }
}
