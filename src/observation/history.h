#pragma once
#include <cstdint>
#include <utility>
#include <functional>
#include <vector>
namespace ndpsim {
// Probes may retain a small history. Production installs a streaming consumer.
template <class T> class History : public std::vector<T> {
public:
    std::function<void(const T&)> sink;
    bool enabled = true;
    uint64_t count = 0;
    void push_back(T value) {
        ++count;
        if (sink)
            sink(value);
        else if (enabled)
            std::vector<T>::push_back(std::move(value));
    }
};
} // namespace ndpsim
