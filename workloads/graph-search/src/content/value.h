#pragma once
// Functional byte values and errors only; no content store, residency or simulator.
#include <cstdint>
#include <cstddef>
#include <stdexcept>
#include <string>
#include <vector>
namespace ndpsim::content {
using Bytes = std::vector<uint8_t>;
enum class Code {
    NotReady,
    InvalidHandle,
    Released,
    WrongLocation,
    PermissionDenied,
    VersionMismatch,
    OutOfRange,
    Overflow,
    InvalidArgument,
    Unsupported,
    DependencyFailed,
    BackendFailure,
    CapacityExceeded
};
const char* name(Code);
struct Context {
    uint64_t query = 0, task = 0, time_ns = 0;
};
struct Error : std::runtime_error {
    Code code;
    Context context;
    uint64_t handle = 0, request = 0;
    Error(Code, std::string, Context = {}, uint64_t handle = 0, uint64_t request = 0);
};
std::string sha256(const Bytes&);
std::string sha256(const uint8_t*, size_t);
} // namespace ndpsim::content
