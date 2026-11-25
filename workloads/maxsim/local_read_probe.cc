#include <sst/core/component.h>
#include <sst/core/link.h>

#include "backend/event_bridge.h"
#include "backend/ordinary_device.h"
#include "build_identity.h"
#include "ssd/Address_Mapping_Unit_Page_Level.h"
#include "ssd/FTL.h"
#include "ssd/TSU_Priority_OutOfOrder.h"
#include "timing/local_reads.h"
#include "timing/resources.h"

#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <tuple>

namespace maxsim {
namespace {
constexpr uint64_t page_bytes = 16 * 1024;
constexpr unsigned page_sectors = page_bytes / 512;
using Target = std::tuple<unsigned, unsigned, unsigned, unsigned>;
constexpr unsigned chip_die_seed_batch = 16;
constexpr unsigned chip_die_seed_depth = 128;
using Physical = NVM::FlashMemory::Physical_Page_Address;

struct Read {
    uint64_t lba;
    Target target;
};

struct LocalWake : SST::Event {
    explicit LocalWake(uint64_t value) : generation(value) {}
    uint64_t generation;
};
} // namespace

class LocalReadProbe : public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(LocalReadProbe, "ndpsim_maxsim", "LocalReadProbe",
                               SST_ELI_ELEMENT_VERSION(0, 4, 5),
                               "MaxSim NAND-to-controller full-page read probe",
                               COMPONENT_CATEGORY_UNCATEGORIZED)
    SST_ELI_DOCUMENT_PARAMS({"device", "Absolute device XML path", ""},
                            {"requests", "Absolute page manifest path", ""},
                            {"preload_sectors", "Aligned preload extent", "0"},
                            {"qd", "Maximum outstanding pages", "1"},
                            {"warmup", "Completions before measurement", "0"},
                            {"die_admission", "Enable experimental local read admission", "false"},
                            {"staging_bytes_per_ns", "FIFO staging bandwidth, zero bypasses staging", "0"},
                            {"seed_gap_ns", "Time between initial 16-channel groups at QD128", "0"})

    LocalReadProbe(SST::ComponentId_t id, SST::Params& params) : SST::Component(id) {
        const auto device_path = params.find<std::string>("device");
        const auto request_path = params.find<std::string>("requests");
        const auto preload = params.find<uint64_t>("preload_sectors");
        qd_ = params.find<unsigned>("qd");
        warmup_ = params.find<unsigned>("warmup");
        die_admission_ = params.find<bool>("die_admission");
        const auto staging_rate = params.find<uint64_t>("staging_bytes_per_ns");
        seed_gap_ = params.find<uint64_t>("seed_gap_ns");
        seed_limit_ = seed_gap_ ? chip_die_seed_batch : qd_;
        ndpsim::DeviceConfig config(device_path, request_path);
        const auto& g = config.geometry();
        if (getNumRanks().rank != 1 || getNumRanks().thread != 1 || g.page_bytes != page_bytes ||
            !qd_ || qd_ > 65536 || !warmup_ || !preload || preload % page_sectors ||
            preload / page_sectors > 65536 ||
            (seed_gap_ && (!die_admission_ || qd_ != chip_die_seed_depth ||
                           g.channels != chip_die_seed_batch || seed_gap_ > 8000)))
            throw std::invalid_argument("local read probe limits or geometry");

        std::ifstream input(request_path);
        std::string line;
        if (!std::getline(input, line) || line != "ndpsim-maxsim-local-pages/1")
            throw std::invalid_argument("local page manifest header");
        while (std::getline(input, line)) {
            std::istringstream fields(line);
            uint64_t lba;
            unsigned channel, chip, die, plane;
            std::string extra;
            if (!(fields >> lba >> channel >> chip >> die >> plane) || fields >> extra ||
                lba % page_sectors || lba >= preload || channel >= g.channels || chip >= g.chips ||
                die >= g.dies || plane >= g.planes || requests_.size() == 65536)
                throw std::invalid_argument("local page manifest row");
            requests_.push_back({lba, {channel, chip, die, plane}});
        }
        if (!input.eof() || requests_.size() <= static_cast<uint64_t>(qd_) + warmup_)
            throw std::invalid_argument("local page manifest coverage");

        std::cout << "LOCAL_DEVICE " << config.effective_json() << '\n';
        std::cout << "LOCAL_BUILD " << mqsim_commit << ' ' << mqsim_source_sha256 << '\n';
        device_ = std::make_unique<ndpsim::OrdinaryDevice>(preload, config);
        link_ = configureSelfLink("wake", "1ns", new SST::Event::Handler<LocalReadProbe, &LocalReadProbe::wake>(this));
        bridge_ = std::make_unique<ndpsim::EventBridge>(
            [this] { return now(); },
            [this](uint64_t delay, uint64_t generation) {
                ++pending_wakes_;
                link_->send(delay, new LocalWake(generation));
            });
        auto& host = *static_cast<SSD_Components::Host_Interface_NVMe*>(device_->device->Host_interface);
        local_ = std::make_unique<ndpsim::timing::MetadataLocalReads>(host, *bridge_, [this] { return now(); });
        if (staging_rate) {
            staging_duration_ = page_bytes / staging_rate + (page_bytes % staging_rate != 0);
            staging_ = std::make_unique<ndpsim::timing::FifoResource>(*bridge_, [this] { return now(); }, 1);
            staging_->observations().sink = [](const auto& record) {
                std::cout << "LOCAL_STAGE " << record.key << ' ' << record.submitted << ' '
                          << record.start << ' ' << record.end << '\n';
            };
        }
        registerAsPrimaryComponent();
        primaryComponentDoNotEndSim();
    }

    ~LocalReadProbe() override {
        if (active_) {
            MQSimObservation::sink = {};
            MQSimObservation::read_queue_sink = {};
        }
    }

    void setup() override {
        device_->start();
        bridge_->start();
    }

    void finish() override {
        if (!active_ || !begin_ || end_ <= begin_ || completed_ != requests_.size() ||
            local_->pending() || local_->pending_requests() || !bridge_->idle() ||
            (staging_ && (staging_->pending() || staged_ != completed_)) ||
            device_->flow->pending() || Simulator->Peek_next_time() || pcie_messages_)
            throw std::logic_error("local read probe did not drain or used Host PCIe");
        std::cout << "LOCAL_DRAIN " << origin_ << ' ' << begin_ << ' ' << end_ << ' ' << now()
                  << ' ' << completed_ << ' ' << pcie_messages_ << '\n';
    }

private:
    uint64_t now() { return getCurrentSimTimeNano(); }

    void begin_reads() {
        auto* ftl = dynamic_cast<SSD_Components::FTL*>(device_->device->Firmware);
        auto* tsu = ftl ? dynamic_cast<SSD_Components::TSU_Priority_OutOfOrder*>(ftl->TSU) : nullptr;
        auto* mapping = ftl ? dynamic_cast<SSD_Components::Address_Mapping_Unit_Page_Level*>(ftl->Address_Mapping_Unit) : nullptr;
        if (!tsu || !mapping || MQSimObservation::sink || MQSimObservation::read_queue_sink || device_->link->observe)
            throw std::logic_error("local read probe requires an isolated page-level FTL and priority out-of-order TSU");

        std::map<uint64_t, Physical> addresses;
        for (const auto& request : requests_) {
            auto [it, inserted] = addresses.try_emplace(request.lba);
            auto& a = it->second;
            if (inserted) {
                PPA_type ppa;
                page_status_type written;
                mapping->Snapshot_mapping(0, request.lba / page_sectors, ppa, written);
                if (ppa == NO_PPA || (written & UINT32_MAX) != UINT32_MAX)
                    throw std::logic_error("local page missing after preload");
                mapping->Convert_ppa_to_address(ppa, a);
                std::cout << "LOCAL_MAP " << request.lba << ' ' << a.ChannelID << ' ' << a.ChipID
                          << ' ' << a.DieID << ' ' << a.PlaneID << ' ' << a.BlockID << ' ' << a.PageID << '\n';
            }
            if (Target{a.ChannelID, a.ChipID, a.DieID, a.PlaneID} != request.target)
                throw std::logic_error("preloaded physical address disagrees with manifest");
        }

        if (die_admission_)
            tsu->Enable_local_read_die_admission();
        origin_ = device_->window->origin();
        active_ = true;
        MQSimObservation::sink = [](const char* kind, uint64_t start, uint64_t end,
                                    unsigned channel, unsigned chip, unsigned die) {
            std::cout << "LOCAL_INTERVAL " << kind << ' ' << start << ' ' << end << ' '
                      << channel << ' ' << chip << ' ' << die << '\n';
        };
        MQSimObservation::read_queue_sink = [](bool dispatched, const auto& e) {
            std::cout << (dispatched ? "LOCAL_DISPATCH " : "LOCAL_QUEUE ") << e.local_id << ' '
                      << e.time << ' ' << e.lpa << ' ' << e.bytes << ' ' << e.channel << ' ' << e.chip
                      << ' ' << e.die << ' ' << e.plane << ' ' << e.block << ' ' << e.page << '\n';
        };
        device_->link->observe = [this](const char*, const auto&) { ++pcie_messages_; };
        for (unsigned limit = seed_limit_ + chip_die_seed_batch; limit <= qd_ && seed_gap_;
             limit += chip_die_seed_batch) {
            const auto delay = (limit / chip_die_seed_batch - 1) * seed_gap_;
            bridge_->enqueue(ndpsim::timing::checked_add(origin_, delay), ndpsim::EventBridge::ContinueSubmit,
                             "seed local pages", [this, limit] {
                seed_limit_ = limit;
                refill();
            });
        }
        refill();
    }

    void refill() {
        while (local_->pending() < qd_ && next_ < requests_.size() &&
               (seed_limit_ == qd_ || next_ < seed_limit_)) {
            auto index = next_++;
            auto lba = requests_[index].lba;
            // Completion releases at this SST tick precede this final refill.
            if (next_ == requests_.size())
                end_ = now();
            local_->submit({page_bytes, page_bytes, {{lba, page_sectors, {}}}},
                           {index, "nand_page", "read"}, [this, lba](auto completion) {
                const auto& request = completion.requests.at(0);
                const auto& tr = request.transactions.at(0);
                if (completion.requests.size() != 1 || request.transactions.size() != 1 ||
                    request.time != now() || tr.time != request.time || request.lba != lba ||
                    tr.lpa != lba / page_sectors || tr.bytes != page_bytes)
                    throw std::logic_error("local completion disagrees with full-page NAND read");
                std::cout << "LOCAL_READ " << completion.context.query << ' ' << tr.id << ' '
                          << lba << ' ' << request.submitted << ' ' << request.time << '\n';
                if (staging_)
                    staging_->submit(std::to_string(completion.context.query), staging_duration_, [] {},
                                     [this](bool success) {
                        if (!success)
                            throw std::logic_error("staging did not complete");
                        ++staged_;
                    });
                if (++completed_ == warmup_)
                    begin_ = now();
                if (next_ < requests_.size())
                    bridge_->enqueue(now(), ndpsim::EventBridge::ContinueSubmit, "refill local pages",
                                     [this] { refill(); });
            });
        }
    }

    void wake(SST::Event* raw) {
        std::unique_ptr<LocalWake> event(static_cast<LocalWake*>(raw));
        --pending_wakes_;
        if (bridge_->wake(event->generation)) {
            if (!active_ && device_->window->advance(!Simulator->Peek_next_time(), now()))
                begin_reads();
            if (active_)
                local_->poll();
            bridge_->require_progress(requests_.size() - completed_, "local read probe");
        }
        if (completed_ == requests_.size() && !pending_wakes_ && bridge_->idle())
            primaryComponentOKToEndSim();
    }

    SST::Link* link_;
    std::unique_ptr<ndpsim::OrdinaryDevice> device_;
    std::unique_ptr<ndpsim::EventBridge> bridge_;
    std::unique_ptr<ndpsim::timing::MetadataLocalReads> local_;
    std::unique_ptr<ndpsim::timing::FifoResource> staging_;
    std::vector<Read> requests_;
    size_t next_ = 0, completed_ = 0, pending_wakes_ = 0, pcie_messages_ = 0;
    uint64_t origin_ = 0, begin_ = 0, end_ = 0;
    uint64_t staging_duration_ = 0, seed_gap_ = 0;
    size_t staged_ = 0;
    unsigned qd_ = 0, warmup_ = 0, seed_limit_ = 0;
    bool active_ = false, die_admission_ = false;
};
} // namespace maxsim
