#include "ssd/Host_Interface_NVMe.h"
#include "ssd/Data_Cache_Manager_Flash_Simple.h"
#include "ssd/NVM_PHY_ONFI.h"
#include "sim/Engine.h"
#include <cstdio>
#include <array>
#include <string>
#include <stdexcept>
using namespace SSD_Components;
void check(bool ok, const char* message) { if (!ok) throw std::logic_error(message); }
template<class F> void rejects(F fn) {
    try { fn(); } catch (const std::exception&) { return; }
    throw std::logic_error("expected rejection");
}
// Control only the service boundary; segmentation, cache aggregation, signal
// fanout, transaction deletion and completion routing are actual MQSim code.
struct ControlledPHY : NVM_PHY_ONFI {
    ControlledPHY() : NVM_PHY_ONFI("phy", 1, 1, 1, 1) {}
    void complete(NVM_Transaction_Flash* tr) { broadcastTransactionServicedSignal(tr); }
    void Setup_triggers() override {}
    void Start_simulation() override {}
    void Validate_simulation_config() override {}
    void Execute_simulator_event(MQSimEngine::Sim_Event*) override {}
    BusChannelStatus Get_channel_status(flash_channel_ID_type) override { throw std::logic_error("unused"); }
    NVM::FlashMemory::Flash_Chip* Get_chip(flash_channel_ID_type, flash_chip_ID_type) override { throw std::logic_error("unused"); }
    LPA_type Get_metadata(flash_channel_ID_type, flash_chip_ID_type, flash_die_ID_type, flash_plane_ID_type, flash_block_ID_type, flash_page_ID_type) override { throw std::logic_error("unused"); }
    bool HasSuspendedCommand(NVM::FlashMemory::Flash_Chip*) override { throw std::logic_error("unused"); }
    ChipStatus GetChipStatus(NVM::FlashMemory::Flash_Chip*) override { throw std::logic_error("unused"); }
    sim_time_type Expected_finish_time(NVM::FlashMemory::Flash_Chip*) override { throw std::logic_error("unused"); }
    void Send_command_to_chip(std::list<NVM_Transaction_Flash*>&) override { throw std::logic_error("unused"); }
    void Change_flash_page_status_for_preconditioning(const NVM::FlashMemory::Physical_Page_Address&, LPA_type) override { throw std::logic_error("unused"); }
    void Change_memory_status_preconditioning(const NVM::NVM_Memory_Address*, const void*) override { throw std::logic_error("unused"); }
};
Caching_Mode modes[] = {Caching_Mode::TURNED_OFF};
struct ControlledCache : Data_Cache_Manager_Flash_Simple {
    ControlledCache(ControlledPHY* phy) : Data_Cache_Manager_Flash_Simple("cache", nullptr, nullptr, phy,
        4096, 1024, 1600, 8, 1, 1, 1, modes, 1, 8, 64) {}
    User_Request* request = nullptr;
    void process_new_user_request(User_Request* req) override { request = req; }
    void replay() { broadcast_user_request_serviced_signal(request); }
};
struct InspectManager : Input_Stream_Manager_NVMe {
    InspectManager(Host_Interface_Base* host) : Input_Stream_Manager_NVMe(host, 4) {}
    std::array<unsigned, 8> queues() const {
        auto* s = static_cast<Input_Stream_NVMe*>(input_streams[0]);
        return {s->Submission_head, s->Submission_head_informed_to_host, s->Submission_tail,
                s->Completion_head, s->Completion_tail, s->On_the_fly_requests,
                unsigned(s->Waiting_user_requests.size()), unsigned(s->Completed_user_requests.size())};
    }
};
struct InspectHost : Host_Interface_NVMe {
    InspectHost(Data_Cache_Manager_Base* cache) : Host_Interface_NVMe("host",1023,8,8,1,4,8,cache) {
        delete input_stream_manager;
        input_stream_manager = new InspectManager(this);
    }
    auto queues() const { return static_cast<InspectManager*>(input_stream_manager)->queues(); }
};
Host_Interface_NVMe* host;
unsigned observed = 0;
void late_observer(NVM_Transaction_Flash* tr) {
    // Invoked after cache has removed the transaction and routed completion.
    check(tr->UserIORequest->ID.size() > 0, "request freed during PHY fanout");
    check(host->Pending_local_reads() == 1, "owner lost request before PHY unwound");
    check(host->Take_local_completions().empty(), "visible inside PHY fanout");
    ++observed;
}
int main(int argc, char** argv) {
    try {
        Simulator->Reset();
        const bool unsupported = argc > 1 && std::string(argv[1]) == "unsupported";
        if (unsupported) modes[0] = Caching_Mode::WRITE_CACHE;
        check(User_Request().Completion_route == RequestCompletionRoute::HOST, "native default route changed");
        ControlledPHY phy;
        ControlledCache cache(&phy);
        InspectHost interface(&cache);
        host = &interface;
        cache.Set_host_interface(host);
        host->Create_new_stream(IO_Flow_Priority_Class::HIGH, 0, 1023, 0, 0);
        Simulator->AddObject(host); Simulator->AddObject(&cache); Simulator->AddObject(&phy);
        Simulator->Initialize_simulation();
        phy.ConnectToTransactionServicedSignal(late_observer);
        const auto original_queues = interface.queues();
        if (unsupported) {
            rejects([&] { host->Submit_local_read(0,7,2); });
            check(!host->Pending_local_reads() && !Simulator->Peek_next_time(), "unsupported cache leaked state");
            Simulator->Reset();
            std::puts("E2 PASS unsupported cache rejected");
            return 0;
        }
        rejects([&] { host->Submit_local_read(1, 7, 2); });
        rejects([&] { host->Submit_local_read(0, 7, 0); });
        rejects([&] { host->Submit_local_read(0, 1023, 2); });
        rejects([&] { host->Submit_local_read(0, 0, UINT32_MAX); });
        check(host->Pending_local_reads() == 0, "invalid input leaked request");
        for (unsigned repeat = 0; repeat != 2; ++repeat) {
            auto token = host->Submit_local_read(0, 7, 2);
            auto* req = cache.request;
            auto id = req->ID;
            check(req->Transaction_list.size() == 2, "cross-page split");
            auto* first = static_cast<NVM_Transaction_Flash_RD*>(req->Transaction_list.front());
            auto* second = static_cast<NVM_Transaction_Flash_RD*>(req->Transaction_list.back());
            check(first->LPA == 0 && second->LPA == 1 && first->Data_and_metadata_size_in_byte == 512
                  && second->Data_and_metadata_size_in_byte == 512
                  && first->read_sectors_bitmap == 128 && second->read_sectors_bitmap == 1, "split extent/bitmap");
            std::printf("E2 submit token=%llu id=%s lba=7 sectors=2 pages=0,1 bytes=512,512\n", token, id.c_str());
            rejects([&] { cache.replay(); }); // defensive premature completion guard
            auto now = repeat * 40;
            Simulator->Synchronize_now(now + 10);
            bool reverse = (argc == 1);
            phy.complete(reverse ? second : first);
            std::printf("E2 transaction token=%llu page=%d time=%u remaining=1 visible=0\n", token, reverse ? 1 : 0, now+10);
            check(req->Transaction_list.size() == 1 && host->Take_local_completions().empty(), "early publication");
            Simulator->Synchronize_now(now + 30);
            phy.complete(reverse ? first : second);
            std::printf("E2 transaction token=%llu page=%d time=%u remaining=0 visible=0\n", token, reverse ? 0 : 1, now+30);
            check(req->Local_completion_queued, "cleanup not queued");
            rejects([&] { cache.replay(); }); // duplicate signal cannot schedule another release
            check(host->Take_local_completions().empty(), "published before cleanup event");
            check(Simulator->Dispatch_current_time(now+30) == 1, "cleanup more than once");
            auto completions = host->Take_local_completions();
            check(completions.size() == 1 && completions[0].token == token && completions[0].request_id == id
                  && completions[0].bytes == 1024 && completions[0].time == now+30, "completion snapshot");
            check(host->Pending_local_reads() == 0 && host->Take_local_completions().empty()
                  && !Simulator->Peek_next_time(), "duplicate notification or retained ownership");
            check(interface.queues() == original_queues, "local route changed SQ/CQ state");
            std::printf("E2 publish token=%llu time=%u released=1 vector_d2h=0 sq=0 cq=0\n", token, now+30);
        }
        check(observed == 4, "late observers missed");
        // No PCIe switch is attached: any ordinary DMA path would fail this test.
        Simulator->Reset();
        std::puts("E2 PASS requests=2 transactions=4 completions=2 releases=2 vector_d2h=0");
    } catch (const std::exception& e) { std::fprintf(stderr, "%s\n", e.what()); return 1; }
}
