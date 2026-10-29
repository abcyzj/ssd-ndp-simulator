#pragma once
#include "exec/Execution_Parameter_Set.h"
#include "exec/SSD_Device.h"
#include "backend/external_flow.h"
#include "backend/preload_window.h"
#include "host/PCIe_Switch.h"
#include "profile.h"
#include <memory>
namespace ndpsim {
// Single native-small device, ordinary NVMe, deterministic page preload.
// No platform link fee: MQSim owns PCIe, data DMA and CQ completion.
class OrdinaryDevice {
public:
    std::unique_ptr<SSD_Device> device;
    std::unique_ptr<Host_Components::PCIe_Link> link;
    std::unique_ptr<Host_Components::PCIe_Switch> sw;
    std::unique_ptr<Host_Components::PCIe_Root_Complex> root;
    std::unique_ptr<ExternalFlow> flow;
    std::unique_ptr<PreloadWindow> window;
    explicit OrdinaryDevice(uint64_t sectors) {
        partition.Channel_No = 2;
        partition.Channel_IDs = channels.data();
        partition.Chip_No = 1;
        partition.Chip_IDs = chips.data();
        partition.Die_No = 2;
        partition.Die_IDs = dies.data();
        partition.Plane_No = 1;
        partition.Plane_IDs = planes.data();
        partition.Priority_Class = IO_Flow_Priority_Class::HIGH;
        partition.Device_Level_Data_Caching_Mode = SSD_Components::Caching_Mode::TURNED_OFF;
        partitions.push_back(&partition);
        std::vector<char> xml(std::begin(lifecycle_xml), std::end(lifecycle_xml));
        rapidxml::xml_document<> doc;
        doc.parse<rapidxml::parse_validate_closing_tags>(xml.data());
        Execution_Parameter_Set config;
        config.XML_deserialize(doc.first_node("Execution_Parameter_Set"));
        Simulator->Reset();
        device = std::make_unique<SSD_Device>(&config.SSD_Device_Configuration, &partitions);
        link = std::make_unique<Host_Components::PCIe_Link>("link", nullptr, nullptr);
        sw = std::make_unique<Host_Components::PCIe_Switch>(link.get(), device->Host_interface);
        root = std::make_unique<Host_Components::PCIe_Root_Complex>(
            link.get(), HostInterface_Types::NVME, nullptr, &flows);
        link->Set_root_complex(root.get());
        link->Set_pcie_switch(sw.get());
        device->Attach_to_host(sw.get());
        flow = std::make_unique<ExternalFlow>(
            root.get(), device->Host_interface->Get_max_logical_sector_address());
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
        if (sectors > flow->Get_end_lsa_address_on_device() + 1)
            throw std::invalid_argument("dataset exceeds native-small namespace");
        window = std::make_unique<PreloadWindow>(*flow, sectors, 8);
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
    std::vector<Host_Components::IO_Flow_Base*> flows;
    std::vector<flash_channel_ID_type> channels{0, 1};
    std::vector<flash_chip_ID_type> chips{0};
    std::vector<flash_die_ID_type> dies{0, 1};
    std::vector<flash_plane_ID_type> planes{0};
    IO_Flow_Parameter_Set_Synthetic partition{};
    std::vector<IO_Flow_Parameter_Set*> partitions;
};
} // namespace ndpsim
