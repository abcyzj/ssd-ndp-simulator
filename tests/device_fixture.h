#pragma once
#include "backend/device_config.h"
#include <stdexcept>

namespace ndpsim::test {
inline DeviceConfig device_fixture(const std::string& name = "native-small") {
    if (name != "native-small" && name != "ann-2g-v1" && name != "ann-16g-v1")
        throw std::invalid_argument("unknown device fixture");
    return DeviceConfig(name + ".xml", std::filesystem::path(NDPSIM_TEST_DEVICE_DIR) / "fixture.json");
}
} // namespace ndpsim::test
