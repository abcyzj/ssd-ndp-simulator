#pragma once
#include "exec/Execution_Parameter_Set.h"
#include "exec/SSD_Device.h"
#include "sim/Engine.h"
#include "profile.h"
#include <memory>
#include <cstdio>
#include <stdexcept>
#include <vector>
#include <fstream>
#include <sstream>
#include "backend/external_flow.h"
#include "backend/preload_window.h"
#include "ssd/FTL.h"
#include "ssd/Address_Mapping_Unit_Page_Level.h"
#include "ssd/Stats.h"
#include "host/PCIe_Switch.h"
class OrdinaryFixture {
public:
    std::unique_ptr<SSD_Device> device;
    std::unique_ptr<Host_Components::PCIe_Link> link;
    std::unique_ptr<Host_Components::PCIe_Switch> sw;
    std::unique_ptr<Host_Components::PCIe_Root_Complex> root;
    std::unique_ptr<ndpsim::ExternalFlow> flow;
    std::vector<Host_Components::IO_Flow_Base*> flows;
    std::vector<flash_channel_ID_type> channels{0,1};
    std::vector<flash_chip_ID_type> chips{0};
    std::vector<flash_die_ID_type> dies{0,1};
    std::vector<flash_plane_ID_type> planes{0};
    IO_Flow_Parameter_Set_Synthetic partition{};
    std::vector<IO_Flow_Parameter_Set*> partitions;
    std::unique_ptr<ndpsim::PreloadWindow> window;
    unsigned done=0, preload=0, target=1;
    bool measuring=false, small_cmt=false, single_read=false;
    unsigned baseline_read_misses=0;
    uint64_t origin=0;
    unsigned page_sectors=8;
    int initial_seed=321;
    struct Request { uint64_t lba; unsigned sectors; };
    std::vector<Request> requests;
    bool explicit_requests=false;
    OrdinaryFixture(bool miss=false,unsigned qd=1,bool single=false,uint64_t read_delay=0,const ndpsim::DeviceConfig* runtime=nullptr,const std::string& request_file=""):target(qd),small_cmt(miss),single_read(single) {
        if(read_delay>1000000000 || (single && qd!=1))throw std::invalid_argument("invalid isolated NAND experiment");
        if(!request_file.empty()) {
            if(!runtime || miss || single || read_delay)throw std::invalid_argument("request manifest requires unmodified external XML");
            if(qd<1 || qd>32 || std::filesystem::file_size(request_file)>4096)throw std::invalid_argument("request manifest budget/count");
            std::ifstream input(request_file);
            std::string line;
            if(!std::getline(input,line) || line!="ndpsim-mqsim-requests/1")throw std::invalid_argument("invalid request manifest header");
            while(std::getline(input,line)) {
                std::istringstream row(line);std::string kind,extra;uint64_t lba,n;
                if(!(row>>kind>>lba>>n) || kind!="read" || n==0 || n>512 || lba>=512 || n>512-lba || row>>extra)
                    throw std::invalid_argument("invalid request manifest row");
                requests.push_back({lba,unsigned(n)});
                if(requests.size()>32)throw std::invalid_argument("request manifest budget exceeded");
            }
            if(requests.size()!=qd || !input.eof())throw std::invalid_argument("request manifest count mismatch");
            explicit_requests=true;
        }
        if(runtime) {
            if(miss || read_delay)throw std::invalid_argument("runtime XML conflicts with test overrides");
            const auto& g=runtime->geometry();
            runtime->validate_layout(512,g.page_bytes,512);
            channels.resize(g.channels);chips.resize(g.chips);dies.resize(g.dies);planes.resize(g.planes);
            for(size_t i=0;i<channels.size();++i)channels[i]=i;
            for(size_t i=0;i<chips.size();++i)chips[i]=i;
            for(size_t i=0;i<dies.size();++i)dies[i]=i;
            for(size_t i=0;i<planes.size();++i)planes[i]=i;
            page_sectors=g.page_sectors;
        }
        partition.Channel_No=channels.size(); partition.Channel_IDs=channels.data();
        partition.Chip_No=chips.size(); partition.Chip_IDs=chips.data();
        partition.Die_No=dies.size(); partition.Die_IDs=dies.data();
        partition.Plane_No=planes.size(); partition.Plane_IDs=planes.data();
        partition.Priority_Class=IO_Flow_Priority_Class::HIGH;
        partition.Device_Level_Data_Caching_Mode=SSD_Components::Caching_Mode::TURNED_OFF;
        partitions.push_back(&partition);
        Execution_Parameter_Set config;
        if(runtime) {
#ifdef NDPSIM_NATIVE_ORACLE
            // The locked upstream XML deserializer independently applies the
            // same raw file; it does not reuse the platform's typed activation.
            std::vector<char> xml(runtime->source_bytes().begin(),runtime->source_bytes().end());xml.push_back(0);
            rapidxml::xml_document<> doc;doc.parse<rapidxml::parse_validate_closing_tags>(xml.data());
            config.XML_deserialize(doc.first_node("Execution_Parameter_Set"));
#else
            runtime->activate();
#endif
        } else {
        std::string text(ndpsim::device_profile("native-small").xml);
        if(miss) text.replace(text.find("<CMT_Capacity>65536"),19,"<CMT_Capacity>128");
        std::vector<char> xml(text.begin(),text.end()); xml.push_back(0);
        rapidxml::xml_document<> doc; doc.parse<rapidxml::parse_validate_closing_tags>(xml.data());
        config.XML_deserialize(doc.first_node("Execution_Parameter_Set"));
        }
        initial_seed=Device_Parameter_Set::Seed;
        if(explicit_requests) {
#define APPLIED(group,key) std::printf("APPLIED " #key "=%.17g\n",double(group::key))
            APPLIED(Host_Parameter_Set,PCIe_Lane_Bandwidth);APPLIED(Host_Parameter_Set,PCIe_Lane_Count);
            APPLIED(Device_Parameter_Set,Seed);APPLIED(Device_Parameter_Set,IO_Queue_Depth);
            APPLIED(Device_Parameter_Set,Queue_Fetch_Size);APPLIED(Device_Parameter_Set,CMT_Capacity);
            APPLIED(Device_Parameter_Set,Flash_Channel_Count);APPLIED(Device_Parameter_Set,Chip_No_Per_Channel);
            APPLIED(Device_Parameter_Set,Overprovisioning_Ratio);
            APPLIED(Flash_Parameter_Set,Die_No_Per_Chip);APPLIED(Flash_Parameter_Set,Plane_No_Per_Die);
            APPLIED(Flash_Parameter_Set,Block_No_Per_Plane);APPLIED(Flash_Parameter_Set,Page_No_Per_Block);
            APPLIED(Flash_Parameter_Set,Page_Capacity);APPLIED(Flash_Parameter_Set,Page_Read_Latency_LSB);
            APPLIED(Flash_Parameter_Set,Page_Read_Latency_CSB);APPLIED(Flash_Parameter_Set,Page_Read_Latency_MSB);
#undef APPLIED
        }
        Flash_Parameter_Set::Page_Read_Latency_LSB+=read_delay;
        Flash_Parameter_Set::Page_Read_Latency_CSB+=read_delay;
        Flash_Parameter_Set::Page_Read_Latency_MSB+=read_delay;
        Simulator->Reset(); device=std::make_unique<SSD_Device>(&config.SSD_Device_Configuration,&partitions);
        link=std::make_unique<Host_Components::PCIe_Link>("link",nullptr,nullptr,Host_Parameter_Set::PCIe_Lane_Bandwidth,Host_Parameter_Set::PCIe_Lane_Count);
        sw=std::make_unique<Host_Components::PCIe_Switch>(link.get(),device->Host_interface);
        root=std::make_unique<Host_Components::PCIe_Root_Complex>(link.get(),HostInterface_Types::NVME,nullptr,&flows);
        link->Set_root_complex(root.get()); link->Set_pcie_switch(sw.get()); device->Attach_to_host(sw.get());
        flow=std::make_unique<ndpsim::ExternalFlow>(root.get(),runtime?runtime->geometry().namespace_sectors-1:device->Host_interface->Get_max_logical_sector_address(),Device_Parameter_Set::IO_Queue_Depth); flows.push_back(flow.get());
#ifndef NDPSIM_NATIVE_ORACLE
        root->data_arrived=[this](uint64_t a,unsigned n){flow->data(a,n);};
#endif
        auto* host=static_cast<SSD_Components::Host_Interface_NVMe*>(device->Host_interface);
        auto* q=flow->Get_nvme_queue_pair_info();
        host->Create_new_stream(IO_Flow_Priority_Class::HIGH,0,flow->Get_end_lsa_address_on_device(),q->Submission_queue_memory_base_address,q->Completion_queue_memory_base_address);
        Simulator->AddObject(link.get()); Simulator->AddObject(flow.get());
        window=std::make_unique<ndpsim::PreloadWindow>(*flow,512,page_sectors);
        if(runtime)std::printf("CONFIG sha256=%s namespace_sectors=%llu page_sectors=%u\n",runtime->source_sha256().c_str(),runtime->geometry().namespace_sectors,page_sectors);
        if(explicit_requests)std::printf("NAMESPACE count=%llu end_lba=%llu\n",
            (unsigned long long)device->Host_interface->Get_max_logical_sector_address(),
            (unsigned long long)flow->Get_end_lsa_address_on_device());
        if(explicit_requests)flow->submitted=[this](uint64_t token,uint64_t lba,unsigned n) {
            std::printf("SUBMIT %llu %llu %u %u %llu\n",token,lba,n,!measuring,Simulator->Time());
        };
        flow->completed=[this](ndpsim::ExternalFlow::Completion c){
            std::printf("IO %llu %llu %u %u %llu\n",c.token,c.lba,c.sectors,c.write,c.time);
            if(measuring) ++done; else ++preload;
        };
    }
    static bool next_time() {
#ifdef NDPSIM_NATIVE_ORACLE
        return Simulator->_EventList->Count!=0;
#else
        return bool(Simulator->Peek_next_time());
#endif
    }
    void start() {
#ifdef NDPSIM_NATIVE_ORACLE
        for(auto& obj:Simulator->_ObjectList) if(!obj.second->IsTriggersSetUp()) obj.second->Setup_triggers();
        for(auto& obj:Simulator->_ObjectList) {obj.second->Validate_simulation_config();obj.second->Start_simulation();}
#else
        Simulator->Initialize_simulation();
#endif
 window->advance(true,Simulator->Time()); }
    // Called after each full time slice: CQ doorbells and all background work drain first.
    void poll() {
        if(!measuring && window->advance(!next_time(),Simulator->Time())) {
            origin=Simulator->Time(); measuring=true;
            std::printf("INITIAL seed=%d preload=0:512 data_cache=off pending=0 time=%llu\n",initial_seed,origin);
            auto* amu=static_cast<SSD_Components::FTL*>(device->Firmware)->Address_Mapping_Unit;
            std::printf("CMT capacity=%u preconditioning_entries=%u misses=%u\n",amu->Get_cmt_capacity(),amu->Get_current_cmt_occupancy_for_stream(0),SSD_Components::Stats::CMT_miss);
            baseline_read_misses=SSD_Components::Stats::readTR_CMT_miss;
#ifdef NDPSIM_NATIVE_ORACLE
            std::ostringstream cache;
            for(auto& e:static_cast<SSD_Components::Address_Mapping_Unit_Page_Level*>(amu)->domains[0]->CMT->lruList)
                cache<<e.first<<":"<<int(e.second->Status)<<":"<<e.second->Dirty<<",";
            std::printf("CACHE %s\n",cache.str().c_str());
#else
            std::printf("CACHE %s\n",static_cast<SSD_Components::Address_Mapping_Unit_Page_Level*>(amu)->Snapshot_cmt(0).c_str());
#endif
            for(unsigned i=0;i<512/page_sectors;++i) {
                PPA_type ppa; page_status_type state;
                auto* page=static_cast<SSD_Components::Address_Mapping_Unit_Page_Level*>(amu);
#ifdef NDPSIM_NATIVE_ORACLE
                ppa=page->domains[0]->GlobalMappingTable[i].PPA;
                state=page->domains[0]->GlobalMappingTable[i].WrittenStateBitmap;
                auto& entries=page->domains[0]->CMT->addressMap;
                auto it=entries.find(LPN_TO_UNIQUE_KEY(0,i));
                if(it!=entries.end() && it->second->Status==SSD_Components::CMTEntryStatus::VALID) {
                    ppa=it->second->PPA;state=it->second->WrittenStateBitmap;
                }
#else
                page->Snapshot_mapping(0,i,ppa,state);
#endif
                std::printf("MAP %u %llu %llu\n",i,(unsigned long long)ppa,(unsigned long long)state);
            }
            if(explicit_requests) {
                stats("initial");
                for(const auto& r:requests)window->read(r.lba,r.sectors);
            } else for(unsigned i=0;i<target;++i) window->read(single_read?0:7+(i%16)*16,single_read?1:10);
        }
    }
    static void stats(const char* phase) {
        std::printf("STATS %s",phase);
#define STAT(key) std::printf(" " #key "=%lu",(unsigned long)SSD_Components::Stats::key)
        STAT(IssuedReadCMD);STAT(IssuedProgramCMD);STAT(IssuedEraseCMD);
        STAT(Total_flash_reads_for_mapping);STAT(Total_flash_writes_for_mapping);
        STAT(CMT_hits);STAT(CMT_miss);STAT(total_CMT_queries);
        STAT(readTR_CMT_hits);STAT(readTR_CMT_miss);STAT(total_readTR_CMT_queries);
        STAT(writeTR_CMT_hits);STAT(writeTR_CMT_miss);STAT(total_writeTR_CMT_queries);
        STAT(Total_gc_executions);STAT(Total_wl_executions);
#undef STAT
        std::puts("");
    }
    void verify() {
        if(!measuring || done!=target || flow->pending()) throw std::logic_error("incomplete ordinary IO");
        auto misses=SSD_Components::Stats::readTR_CMT_miss-baseline_read_misses;
        if(small_cmt && !misses) throw std::logic_error("missing read CMT miss coverage");
        if(explicit_requests) {
            if(next_time())throw std::logic_error("backend not drained");
            stats("final");
            std::printf("DRAIN pending=%zu events=%u preload=%u time=%llu\n",flow->pending(),unsigned(next_time()),preload,Simulator->Time());
        }
        std::printf("FINAL reads=%u read_cmt_misses=%u origin=%llu\n",done,misses,origin);
    }
    ~OrdinaryFixture(){ Simulator->Reset(); flow.reset(); device.reset(); }
};
