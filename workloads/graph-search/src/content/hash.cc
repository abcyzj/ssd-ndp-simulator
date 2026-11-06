#include "content/value.h"
#include <ndpsim/capture.h>
namespace ndpsim::content {
std::string sha256(const uint8_t* data, size_t size) {
    return capture::sha256(size ? std::string_view(reinterpret_cast<const char*>(data), size) : std::string_view{});
}
std::string sha256(const Bytes& bytes) { return sha256(bytes.data(), bytes.size()); }
}
