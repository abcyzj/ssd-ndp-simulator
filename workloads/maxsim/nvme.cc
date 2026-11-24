#include <sst/core/component.h>
#include <sst/core/link.h>

#include "backend/event_bridge.h"
#include "backend/ordinary_device.h"

#include <fstream>
#include <map>
#include <sstream>
#include <vector>

namespace maxsim {
namespace {
struct NvmeWake : SST::Event {
    explicit NvmeWake(uint64_t value) : generation(value) {}
    uint64_t generation;
};

struct Read {
    uint64_t lba;
    unsigned sectors;
};

struct PendingRead {
    Read read;
    uint64_t submitted;
};
} // namespace

class NvmeProbe : public SST::Component {
public:
    SST_ELI_REGISTER_COMPONENT(NvmeProbe, "ndpsim_maxsim", "NvmeProbe",
                               SST_ELI_ELEMENT_VERSION(0, 4, 5),
                               "MaxSim ordinary read calibration",
                               COMPONENT_CATEGORY_UNCATEGORIZED)
    SST_ELI_DOCUMENT_PARAMS({"device", "Absolute device XML path", ""},
                            {"requests", "Absolute read manifest path", ""},
                            {"preload_sectors", "Aligned preload extent", "0"},
                            {"qd", "Maximum outstanding reads", "1"})

    NvmeProbe(SST::ComponentId_t id, SST::Params& params) : SST::Component(id) {
        const auto device_path = params.find<std::string>("device");
        const auto request_path = params.find<std::string>("requests");
        const auto preload = params.find<uint64_t>("preload_sectors");
        qd_ = params.find<unsigned>("qd");
        ndpsim::DeviceConfig config(device_path, request_path);
        const auto& geometry = config.geometry();
        if (getNumRanks().rank != 1 || getNumRanks().thread != 1 || !qd_ || qd_ >= geometry.queue_depth ||
            !preload || preload / geometry.page_sectors > 1'000'000)
            throw std::invalid_argument("ordinary read calibration limits");
        std::ifstream input(request_path);
        std::string line;
        if (!std::getline(input, line) || line != "ndpsim-maxsim-nvme-reads/1")
            throw std::invalid_argument("ordinary read manifest header");
        while (std::getline(input, line)) {
            std::istringstream fields(line);
            Read read{};
            std::string extra;
            if (!(fields >> read.lba >> read.sectors) || fields >> extra || !read.sectors ||
                read.sectors > 256 || read.lba >= preload || read.sectors > preload - read.lba ||
                requests_.size() == 65'536)
                throw std::invalid_argument("ordinary read manifest row");
            requests_.push_back(read);
        }
        if (!input.eof() || requests_.size() < qd_)
            throw std::invalid_argument("ordinary read manifest coverage");
        std::printf("CAL_DEVICE %s\n", config.effective_json().c_str());
        device_ = std::make_unique<ndpsim::OrdinaryDevice>(preload, config);
        device_->flow->completed = [this](ndpsim::ExternalFlow::Completion record) {
            if (!measuring_) {
                if (!record.write)
                    throw std::logic_error("read completed during preload");
                return;
            }
            auto pending = outstanding_.find(record.token);
            if (pending == outstanding_.end() || record.write ||
                pending->second.read.lba != record.lba || pending->second.read.sectors != record.sectors ||
                record.time != now() || record.time <= pending->second.submitted)
                throw std::logic_error("ordinary read completion disagreement");
            std::printf("CAL_READ %llu %llu %u %llu %llu\n", num(record.token), num(record.lba),
                        record.sectors, num(pending->second.submitted), num(record.time));
            outstanding_.erase(pending);
            ++completed_;
            last_completion_ = record.time;
        };
        link_ = configureSelfLink("wake", "1ns", new SST::Event::Handler<NvmeProbe, &NvmeProbe::wake>(this));
        bridge_ = std::make_unique<ndpsim::EventBridge>(
            [this] { return now(); },
            [this](uint64_t delay, uint64_t generation) {
                ++pending_wakes_;
                link_->send(delay, new NvmeWake(generation));
            });
        registerAsPrimaryComponent();
        primaryComponentDoNotEndSim();
    }

    void setup() override {
        device_->start();
        bridge_->start();
    }

    void finish() override {
        if (!measuring_ || completed_ != requests_.size() || !outstanding_.empty() ||
            !bridge_->idle() || device_->flow->pending() || Simulator->Peek_next_time())
            throw std::logic_error("ordinary read calibration did not drain");
        std::printf("CAL_DRAIN %llu %llu %zu\n", num(origin_), num(last_completion_), completed_);
    }

private:
    static unsigned long long num(uint64_t value) { return static_cast<unsigned long long>(value); }
    uint64_t now() { return getCurrentSimTimeNano(); }

    void wake(SST::Event* raw) {
        std::unique_ptr<NvmeWake> event(static_cast<NvmeWake*>(raw));
        --pending_wakes_;
        if (bridge_->wake(event->generation)) {
            if (!measuring_ && device_->window->advance(!Simulator->Peek_next_time(), now())) {
                measuring_ = true;
                origin_ = device_->window->origin();
            }
            if (measuring_) {
                while (outstanding_.size() < qd_ && next_ < requests_.size()) {
                    const auto read = requests_[next_++];
                    const auto token = device_->window->read(read.lba, read.sectors);
                    outstanding_.emplace(token, PendingRead{read, now()});
                }
            }
            bridge_->require_progress(requests_.size() - completed_, "ordinary read calibration");
        }
        if (completed_ == requests_.size() && !pending_wakes_ && bridge_->idle())
            primaryComponentOKToEndSim();
    }

    SST::Link* link_;
    std::unique_ptr<ndpsim::OrdinaryDevice> device_;
    std::unique_ptr<ndpsim::EventBridge> bridge_;
    std::vector<Read> requests_;
    std::map<uint64_t, PendingRead> outstanding_;
    size_t next_ = 0, completed_ = 0, pending_wakes_ = 0;
    uint64_t origin_ = 0, last_completion_ = 0;
    unsigned qd_ = 0;
    bool measuring_ = false;
};
} // namespace maxsim
