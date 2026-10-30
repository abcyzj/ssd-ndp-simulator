#include <sst/core/sst_config.h>
#include <sst/core/component.h>
#include <sst/core/link.h>
#include "backend/ordinary_device.h"
#include "backend/event_bridge.h"
#include "backend/observation.h"
#include <chrono>
#include <sys/resource.h>

namespace ndpsim {
// Resource-only probe. Does not expose a new application device profile or fast init.
class M0Preload : public SST::Component {
    class Wake : public SST::Event {
    public:
        uint64_t generation;
        explicit Wake(uint64_t g) : generation(g) {}
    };
public:
    SST_ELI_REGISTER_COMPONENT(M0Preload, "ndpsim", "M0Preload", SST_ELI_ELEMENT_VERSION(0, 2, 0),
                               "T0 isolated full-page initialization resource probe", COMPONENT_CATEGORY_UNCATEGORIZED)
    SST_ELI_DOCUMENT_PARAMS({"bytes", "Page-aligned preload bytes", "67108864"},
                            {"blocks", "Probe-only blocks/plane (2GiB or 16GiB raw)", "2048"})
    M0Preload(SST::ComponentId_t id, SST::Params& p) : SST::Component(id) {
        bytes_ = p.find<uint64_t>("bytes", 67108864);
        unsigned blocks = p.find<unsigned>("blocks", 2048);
        if ((blocks != 2048 && blocks != 16384) || !bytes_ || bytes_ % 4096 ||
            bytes_ > uint64_t(blocks) * 4 * 64 * 4096 * 9 / 10)
            throw std::invalid_argument("invalid M0 probe geometry/layout");
        std::string xml(lifecycle_xml);
        const std::string original = "<Block_No_Per_Plane>64</Block_No_Per_Plane>";
        auto at = xml.find(original);
        if (at == std::string::npos) throw std::logic_error("native profile changed");
        xml.replace(at, original.size(), "<Block_No_Per_Plane>" + std::to_string(blocks) + "</Block_No_Per_Plane>");
        // Use the exact ordinary-device setup with a probe-local XML copy.
        partition_.Channel_No=2; partition_.Channel_IDs=channels_.data();
        partition_.Chip_No=1; partition_.Chip_IDs=chips_.data();
        partition_.Die_No=2; partition_.Die_IDs=dies_.data();
        partition_.Plane_No=1; partition_.Plane_IDs=planes_.data();
        partition_.Priority_Class=IO_Flow_Priority_Class::HIGH;
        partition_.Device_Level_Data_Caching_Mode=SSD_Components::Caching_Mode::TURNED_OFF;
        partitions_.push_back(&partition_);
        std::vector<char> text(xml.begin(),xml.end()); text.push_back(0);
        rapidxml::xml_document<> doc; doc.parse<rapidxml::parse_validate_closing_tags>(text.data());
        Execution_Parameter_Set config; config.XML_deserialize(doc.first_node("Execution_Parameter_Set"));
        Simulator->Reset();
        device_=std::make_unique<SSD_Device>(&config.SSD_Device_Configuration,&partitions_);
        link_=std::make_unique<Host_Components::PCIe_Link>("link",nullptr,nullptr);
        sw_=std::make_unique<Host_Components::PCIe_Switch>(link_.get(),device_->Host_interface);
        root_=std::make_unique<Host_Components::PCIe_Root_Complex>(link_.get(),HostInterface_Types::NVME,nullptr,&flows_);
        link_->Set_root_complex(root_.get()); link_->Set_pcie_switch(sw_.get()); device_->Attach_to_host(sw_.get());
        namespace_sectors_=device_->Host_interface->Get_max_logical_sector_address()+1;
        if(bytes_/512>namespace_sectors_) throw std::invalid_argument("layout exceeds actual namespace");
        flow_=std::make_unique<ExternalFlow>(root_.get(),namespace_sectors_-1); flows_.push_back(flow_.get());
        root_->data_arrived=[this](uint64_t a,unsigned n){flow_->data(a,n);};
        auto* host=static_cast<SSD_Components::Host_Interface_NVMe*>(device_->Host_interface);
        auto* q=flow_->Get_nvme_queue_pair_info();
        host->Create_new_stream(IO_Flow_Priority_Class::HIGH,0,flow_->Get_end_lsa_address_on_device(),q->Submission_queue_memory_base_address,q->Completion_queue_memory_base_address);
        Simulator->AddObject(link_.get()); Simulator->AddObject(flow_.get());
        window_=std::make_unique<PreloadWindow>(*flow_,bytes_/512,8);
        flow_->completed=[this](ExternalFlow::Completion c) {
            if(!c.write || c.sectors!=8 || c.lba!=writes_*8) throw std::logic_error("preload write sequence mismatch");
            ++writes_;
        };
        observation_=std::make_unique<Observation>(); // Includes existing preload history retention.
        wake_=configureSelfLink("wake","1ns",new SST::Event::Handler<M0Preload,&M0Preload::wake>(this));
        bridge_=std::make_unique<EventBridge>([this]{return getCurrentSimTimeNano();},[this](uint64_t delay,uint64_t g){++pending_;wake_->send(delay,new Wake(g));});
        registerAsPrimaryComponent(); primaryComponentDoNotEndSim();
    }
    void setup() override {
        Simulator->Initialize_simulation(); window_->advance(true,Simulator->Time()); bridge_->start();
    }
    void finish() override {
        if(!ready_ || writes_!=bytes_/4096 || flow_->pending() || Simulator->Peek_next_time())
            throw std::logic_error("preload not drained");
        std::printf("M0_PRELOAD {\"bytes\":%llu,\"writes\":%llu,\"namespace_sectors\":%llu,\"origin_ns\":%llu,\"init_wall_seconds\":%.9f,\"drained\":true,\"cmt_retained\":true}\n",
                    bytes_,writes_,namespace_sectors_,window_->origin(),
                    std::chrono::duration<double>(std::chrono::steady_clock::now()-start_).count());
        observation_.reset(); bridge_.reset(); Simulator->Reset(); flow_.reset(); device_.reset();
    }
private:
    void wake(SST::Event* raw) {
        std::unique_ptr<Wake> e(static_cast<Wake*>(raw)); --pending_;
        bridge_->wake(e->generation);
        if(!ready_) ready_=window_->advance(!Simulator->Peek_next_time(),getCurrentSimTimeNano());
        bridge_->require_progress(!ready_,"M0 preload");
        if(ready_ && !pending_ && bridge_->idle()) primaryComponentOKToEndSim();
    }
    std::chrono::steady_clock::time_point start_=std::chrono::steady_clock::now();
    uint64_t bytes_=0,writes_=0,namespace_sectors_=0;
    unsigned pending_=0;
    bool ready_=false;
    SST::Link* wake_=nullptr;
    std::vector<flash_channel_ID_type> channels_{0,1};
    std::vector<flash_chip_ID_type> chips_{0};
    std::vector<flash_die_ID_type> dies_{0,1};
    std::vector<flash_plane_ID_type> planes_{0};
    IO_Flow_Parameter_Set_Synthetic partition_{};
    std::vector<IO_Flow_Parameter_Set*> partitions_;
    std::vector<Host_Components::IO_Flow_Base*> flows_;
    std::unique_ptr<SSD_Device> device_;
    std::unique_ptr<Host_Components::PCIe_Link> link_;
    std::unique_ptr<Host_Components::PCIe_Switch> sw_;
    std::unique_ptr<Host_Components::PCIe_Root_Complex> root_;
    std::unique_ptr<ExternalFlow> flow_;
    std::unique_ptr<PreloadWindow> window_;
    std::unique_ptr<Observation> observation_;
    std::unique_ptr<EventBridge> bridge_;
};
} // namespace ndpsim
