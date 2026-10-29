#pragma once
#include "host/IO_Flow_Base.h"
#include "sim/Engine.h"
#include <functional>
#include <map>

#include <stdexcept>
namespace ndpsim {
// One namespace, ordinary NVMe queue; callers own admission and SST scheduling.
class ExternalFlow : public Host_Components::IO_Flow_Base {
public:
    struct Completion {
        uint64_t token, lba, time;
        unsigned sectors;
        bool write;
    };
    std::function<void(Completion)> completed;
    std::function<void(uint64_t, uint64_t, unsigned)> submitted;
    ExternalFlow(Host_Components::PCIe_Root_Complex* root, uint64_t end)
        : IO_Flow_Base("external",
                       0,
                       0,
                       end,
                       1,
                       16,
                       16,
                       IO_Flow_Priority_Class::HIGH,
                       UINT64_MAX,
                       0,
                       0,
                       HostInterface_Types::NVME,
                       root,
                       nullptr,
                       false,
                       1000000,
                       "") {
    }
    Host_Components::Host_IO_Request* Generate_next_request() override {
        return nullptr;
    }
    void Get_statistics(Utils::Workload_Statistics&,
                        LPA_type (*)(LHA_type),
                        page_status_type (*)(LHA_type)) override {
        throw std::logic_error("Unsupported: external flow statistical preconditioning");
    }
    void Validate_simulation_config() override {
    }
    void Execute_simulator_event(MQSimEngine::Sim_Event*) override {
        throw std::logic_error("unexpected flow event");
    }
    uint64_t submit(uint64_t lba, unsigned sectors, bool write = false) {
        if (!sectors || sectors > 65535 || lba > end_lsa_on_device ||
            sectors - 1 > end_lsa_on_device - lba)
            throw std::invalid_argument("invalid external extent");
        auto* r = new Host_Components::Host_IO_Request{};
        r->Start_LBA = lba;
        r->LBA_count = sectors;
        r->Arrival_time = Simulator->Time();
        r->Type = write ? Host_Components::Host_IO_Request_Type::WRITE
                        : Host_Components::Host_IO_Request_Type::READ;
        auto token = ++sequence_;
        records_.emplace(r, Record{token, 0});
        if (submitted)
            submitted(token, lba, sectors);
        ++STAT_generated_request_count;
        Submit_io_request(r);
        return token;
    }
    Submission_Queue_Entry* NVMe_read_sqe(uint64_t address)
#ifndef NDPSIM_NATIVE_ORACLE
        override
#endif
    {
        auto* sqe = IO_Flow_Base::NVMe_read_sqe(address);
        // A distinct virtual DMA region per command. Address does not affect link cost.
        sqe->PRP_entry_1 =
            DATA_MEMORY_REGION + (uint64_t(sqe->Command_Identifier) + 1) * 0x2000000ULL;
        return sqe;
    }
    void data(uint64_t address, unsigned bytes) {
        auto cmd = (address - DATA_MEMORY_REGION) / 0x2000000ULL;
        if (!cmd)
            throw std::logic_error("invalid DMA address");
        auto it = nvme_software_request_queue.find(cmd - 1);
        if (it == nvme_software_request_queue.end() || !it->second)
            throw std::logic_error("orphan DMA");
        auto& record = records_.at(it->second);
        record.bytes += bytes;
        if (record.bytes != uint64_t(it->second->LBA_count) * 512)
            throw std::logic_error("DMA byte mismatch");
    }
    void NVMe_consume_io_request(Completion_Queue_Entry* cqe) override {
        auto it = nvme_software_request_queue.find(cqe->Command_Identifier);
        if (it == nvme_software_request_queue.end() || !it->second)
            throw std::logic_error("orphan CQ");
        auto* r = it->second;
        auto record = records_.at(r);
        bool write = r->Type == Host_Components::Host_IO_Request_Type::WRITE;
#ifndef NDPSIM_NATIVE_ORACLE
        if (!write && record.bytes != uint64_t(r->LBA_count) * 512)
            throw std::logic_error("CQ before data");
#endif
        Completion result{record.token, r->Start_LBA, Simulator->Time(), r->LBA_count, write};
        records_.erase(r);
        IO_Flow_Base::NVMe_consume_io_request(cqe);
        NVMe_update_and_submit_completion_queue_tail();
        if (completed)
            completed(result);
    }
    size_t pending() const {
        return records_.size();
    }

private:
    struct Record {
        uint64_t token, bytes;
    };
    uint64_t sequence_ = 0;
    std::map<Host_Components::Host_IO_Request*, Record> records_;
};
} // namespace ndpsim
