#include "backend/lifecycle.h"
#include "profile.h"
#include "exec/Execution_Parameter_Set.h"
#include "exec/SSD_Device.h"
#include "sim/Engine.h"
#include <stdexcept>
#include <vector>

namespace ndpsim {
namespace {
bool device_claimed = false;
}

struct LifecycleBackend::Impl {
    // Partition arrays remain alive until after SSD teardown. MQSim copies these
    // IDs into its mapping domain; it does not own these input arrays.
    std::vector<flash_channel_ID_type> channels{0, 1};
    std::vector<flash_chip_ID_type> chips{0};
    std::vector<flash_die_ID_type> dies{0, 1};
    std::vector<flash_plane_ID_type> planes{0};
    IO_Flow_Parameter_Set_Synthetic partition{};
    std::vector<IO_Flow_Parameter_Set*> partitions;
    std::unique_ptr<SSD_Device> device;
    bool initialized = false;
    bool finished = false;

    Impl() {
        // One inactive namespace defines address/resource partitioning. No host
        // flow, PCIe attachment, workload, or application request exists yet.
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
        std::vector<char> xml(std::begin(lifecycle_xml), std::end(lifecycle_xml));
        rapidxml::xml_document<> doc;
        doc.parse<rapidxml::parse_validate_closing_tags>(xml.data());
        Execution_Parameter_Set config;
        config.XML_deserialize(doc.first_node("Execution_Parameter_Set"));
        Simulator->Reset();
        device = std::make_unique<SSD_Device>(&config.SSD_Device_Configuration, &partitions);
    }

    ~Impl() {
        // Clear the engine's non-owning registry before deleting the device
        // graph, including on early finish. In-flight I/O is not supported.
        if (device) {
            Simulator->Reset();
            device.reset();
        }
    }
};

LifecycleBackend::LifecycleBackend(unsigned device_count, const std::string& profile) {
    if (device_count != 1)
        throw std::invalid_argument("Unsupported: device_count must be 1");
    if (profile != "native-small")
        throw std::invalid_argument("Unsupported: profile must be native-small");
    if (device_claimed)
        throw std::logic_error("Only one MQSim device lifetime per process is supported");
    device_claimed = true; // Intentionally never released: singleton state is not reusable.
    impl_ = std::make_unique<Impl>();
}
LifecycleBackend::~LifecycleBackend() = default;
void LifecycleBackend::initialize() {
    if (impl_->finished || impl_->initialized)
        throw std::logic_error("Invalid backend initialize transition");
    Simulator->Initialize_simulation();
    if (Simulator->Time() != 0)
        throw std::logic_error("Initialization advanced MQSim time");
    impl_->initialized = true;
}
void LifecycleBackend::finish() {
    if (impl_->finished)
        return;
    if (!impl_->initialized)
        throw std::logic_error("Backend finish before initialization");
    if (Simulator->Peek_next_time())
        throw std::logic_error("Backend finish with pending events");
    Simulator->Reset();
    impl_->device.reset();
    impl_->finished = true;
}
uint64_t LifecycleBackend::time_ns() const {
    return Simulator->Time();
}
} // namespace ndpsim
