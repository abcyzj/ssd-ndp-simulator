#pragma once
#include "backend/external_flow.h"
namespace ndpsim {
// Deterministic layout preload, independent of future application reads.
// Owner calls advance only after the complete backend queue has drained.
// Measurement uses an origin offset; the backend clock and cumulative stats never reset.
class PreloadWindow {
public:
    PreloadWindow(ExternalFlow& flow, uint64_t sectors, unsigned page_sectors)
        : flow_(flow), sectors_(sectors), page_(page_sectors) {
        if (!page_ || !sectors_ || sectors_ % page_)
            throw std::invalid_argument("preload must contain whole pages");
    }
    bool advance(bool backend_idle, uint64_t now) {
        if (ready_)
            return true;
        if (!backend_idle || flow_.pending())
            return false;
        if (next_ < sectors_) {
            flow_.submit(next_, page_, true);
            next_ += page_;
            return false;
        }
        ready_ = true;
        origin_ = now;
        return true;
    }
    uint64_t read(uint64_t lba, unsigned sectors) {
        if (!ready_)
            throw std::logic_error("measurement before preload drain");
        if (!sectors || lba >= sectors_ || sectors > sectors_ - lba)
            throw std::invalid_argument("read outside preload layout");
        return flow_.submit(lba, sectors);
    }
    uint64_t completed_sectors() const {
        if (!ready_)
            throw std::logic_error("preload not drained");
        return next_;
    }
    uint64_t origin() const {
        if (!ready_)
            throw std::logic_error("window not ready");
        return origin_;
    }

private:
    ExternalFlow& flow_;
    uint64_t sectors_, next_ = 0, origin_ = 0;
    unsigned page_;
    bool ready_ = false;
};
} // namespace ndpsim
