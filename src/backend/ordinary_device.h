#pragma once
#include "exec/Execution_Parameter_Set.h"
#include "exec/SSD_Device.h"
#include "backend/external_flow.h"
#include "backend/preload_window.h"
#include "host/PCIe_Switch.h"
#include "backend/device_config.h"
#include "utils/Logical_Address_Partitioning_Unit.h"
#include <memory>
namespace ndpsim {
// Single controlled-profile device, ordinary NVMe, deterministic page preload.
// No platform link fee: MQSim owns PCIe, data DMA and CQ completion.
class OrdinaryDevice {
public:
    std::unique_ptr<SSD_Device> device;
    std::unique_ptr<Host_Components::PCIe_Link> link;
    std::unique_ptr<Host_Components::PCIe_Switch> sw;
    std::unique_ptr<Host_Components::PCIe_Root_Complex> root;
    std::unique_ptr<ExternalFlow> flow;
    std::unique_ptr<PreloadWindow> window;
    uint64_t namespace_sectors = 0;
    OrdinaryDevice(uint64_t sectors,
                   const DeviceConfig& configuration,
                   const layout::Layout& layout)
        : OrdinaryDevice(sectors, checked_layout(configuration, layout, sectors)) {
    }
    OrdinaryDevice(uint64_t sectors,
                   const DeviceConfig& configuration,
                   uint64_t layout_capacity = 0,
                   uint64_t layout_page_bytes = 0) {
        const auto& profile = configuration.geometry();
        configuration.validate_layout(layout_capacity ? layout_capacity : sectors,
                                      layout_page_bytes ? layout_page_bytes : profile.page_bytes,
                                      sectors);
        channels.resize(profile.channels);
        chips.resize(profile.chips);
        dies.resize(profile.dies);
        planes.resize(profile.planes);
        for (size_t i = 0; i < channels.size(); ++i)
            channels[i] = i;
        for (size_t i = 0; i < chips.size(); ++i)
            chips[i] = i;
        for (size_t i = 0; i < dies.size(); ++i)
            dies[i] = i;
        for (size_t i = 0; i < planes.size(); ++i)
            planes[i] = i;
        partition.Channel_No = channels.size();
        partition.Channel_IDs = channels.data();
        partition.Chip_No = chips.size();
        partition.Chip_IDs = chips.data();
        partition.Die_No = dies.size();
        partition.Die_IDs = dies.data();
        partition.Plane_No = planes.size();
        partition.Plane_IDs = planes.data();
        partition.Priority_Class = IO_Flow_Priority_Class::HIGH;
        partition.Device_Level_Data_Caching_Mode = SSD_Components::Caching_Mode::TURNED_OFF;
        partitions.push_back(&partition);
        configuration.activate();
        Execution_Parameter_Set config;
        Simulator->Reset();
        device = std::make_unique<SSD_Device>(&config.SSD_Device_Configuration, &partitions);
        link = std::make_unique<Host_Components::PCIe_Link>(
            "link", nullptr, nullptr, profile.lane_bandwidth, profile.lanes);
        sw = std::make_unique<Host_Components::PCIe_Switch>(link.get(), device->Host_interface);
        root = std::make_unique<Host_Components::PCIe_Root_Complex>(
            link.get(), HostInterface_Types::NVME, nullptr, &flows);
        link->Set_root_complex(root.get());
        link->Set_pcie_switch(sw.get());
        device->Attach_to_host(sw.get());
        flow = std::make_unique<ExternalFlow>(
            root.get(), profile.namespace_sectors - 1, profile.queue_depth);
        flows.push_back(flow.get());
        root->data_arrived = [this](uint64_t a, unsigned n) {
            flow->data(a, n);
        };
        auto* host = static_cast<SSD_Components::Host_Interface_NVMe*>(device->Host_interface);
        auto* q = flow->Get_nvme_queue_pair_info();
        host->Create_new_stream(IO_Flow_Priority_Class::HIGH,
                                0,
                                flow->Get_end_lsa_address_on_device(),
                                q->Submission_queue_memory_base_address,
                                q->Completion_queue_memory_base_address);
        Simulator->AddObject(link.get());
        Simulator->AddObject(flow.get());
        namespace_sectors = flow->Get_end_lsa_address_on_device() + 1;
        if (namespace_sectors != profile.namespace_sectors || sectors > namespace_sectors ||
            device->Host_interface->Get_max_logical_sector_address() != profile.namespace_sectors ||
            Utils::Logical_Address_Partitioning_Unit::End_lha_available_to_flow(0) + 1 !=
                profile.namespace_sectors)
            throw std::invalid_argument("layout/preload exceeds actual MQSim namespace");
        window = std::make_unique<PreloadWindow>(*flow, sectors, profile.page_sectors);
    }
    void start() {
        Simulator->Initialize_simulation();
        window->advance(true, Simulator->Time());
    }
    ~OrdinaryDevice() {
        Simulator->Reset();
        flow.reset();
        device.reset();
    }

private:
    static const DeviceConfig&
    checked_layout(const DeviceConfig& config, const layout::Layout& layout, uint64_t sectors) {
        config.validate_layout(layout, sectors);
        return config;
    }
    std::vector<Host_Components::IO_Flow_Base*> flows;
    std::vector<flash_channel_ID_type> channels{0, 1};
    std::vector<flash_chip_ID_type> chips{0};
    std::vector<flash_die_ID_type> dies{0, 1};
    std::vector<flash_plane_ID_type> planes{0};
    IO_Flow_Parameter_Set_Synthetic partition{};
    std::vector<IO_Flow_Parameter_Set*> partitions;
};
} // namespace ndpsim
