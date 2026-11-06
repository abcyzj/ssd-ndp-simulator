#include "content/value.h"
namespace ndpsim::content {
const char* name(Code c) {
    switch (c) {
#define CASE(x)                                                                                    \
    case Code::x:                                                                                  \
        return #x;
        CASE(NotReady)
        CASE(InvalidHandle)
        CASE(Released)
        CASE(WrongLocation)
        CASE(PermissionDenied) CASE(VersionMismatch) CASE(OutOfRange) CASE(Overflow)
            CASE(InvalidArgument) CASE(Unsupported) CASE(DependencyFailed) CASE(BackendFailure)
                CASE(CapacityExceeded)
#undef CASE
    }
    return "Unknown";
}
Error::Error(Code c, std::string detail, Context ctx, uint64_t h, uint64_t r)
    : std::runtime_error(std::string(name(c)) + ": " + detail), code(c), context(ctx), handle(h),
      request(r) {
}
}
