#include "exec/Execution_Parameter_Set.h"
#include "exec/SSD_Device.h"
#include "sim/Engine.h"
#include "profile.h"
#include <memory>
#include <cstdio>
#include <stdexcept>
#include <vector>
int main() {
    try {
        std::vector<flash_channel_ID_type> channels{0,1};
        std::vector<flash_chip_ID_type> chips{0};
        std::vector<flash_die_ID_type> dies{0,1};
        std::vector<flash_plane_ID_type> planes{0};
        IO_Flow_Parameter_Set_Synthetic partition{};
        partition.Channel_No=2; partition.Channel_IDs=channels.data();
        partition.Chip_No=1; partition.Chip_IDs=chips.data();
        partition.Die_No=2; partition.Die_IDs=dies.data();
        partition.Plane_No=1; partition.Plane_IDs=planes.data();
        partition.Priority_Class=IO_Flow_Priority_Class::HIGH;
        partition.Device_Level_Data_Caching_Mode=SSD_Components::Caching_Mode::TURNED_OFF;
        std::vector<IO_Flow_Parameter_Set*> partitions{&partition};
        std::vector<char> xml(std::begin(ndpsim::lifecycle_xml), std::end(ndpsim::lifecycle_xml));
        rapidxml::xml_document<> doc; doc.parse<rapidxml::parse_validate_closing_tags>(xml.data());
        Execution_Parameter_Set config; config.XML_deserialize(doc.first_node("Execution_Parameter_Set"));
        Simulator->Reset();
        auto device=std::make_unique<SSD_Device>(&config.SSD_Device_Configuration,&partitions);
        auto* host=static_cast<SSD_Components::Host_Interface_NVMe*>(device->Host_interface);
        host->Create_new_stream(IO_Flow_Priority_Class::HIGH, 0, host->Get_max_logical_sector_address(), 0, 0);
        Simulator->Initialize_simulation();
        auto token=host->Submit_local_read(0,7,2);
        if (!host->Take_local_completions().empty()) throw std::logic_error("early device result");
        unsigned steps=0, count=0;
        while (auto next=Simulator->Peek_next_time()) {
            if (++steps > 10000) throw std::logic_error("device stall");
            Simulator->Dispatch_current_time(*next);
            for (const auto& result:host->Take_local_completions()) {
                if (result.token!=token || result.bytes!=1024 || !result.time || host->Pending_local_reads())
                    throw std::logic_error("device completion mismatch");
                ++count;
                std::printf("E2 NAND token=%llu bytes=%u time=%llu vector_d2h=0\n",result.token,result.bytes,result.time);
            }
        }
        if (count!=1 || host->Pending_local_reads()) throw std::logic_error("device lifetime mismatch");
        Simulator->Reset(); device.reset();
        std::puts("E2 NAND PASS completions=1 no PCIe attachment");
    } catch (const std::exception& e) { std::fprintf(stderr,"%s\n",e.what()); return 1; }
}
