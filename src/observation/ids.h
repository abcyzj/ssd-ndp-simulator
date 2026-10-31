#pragma once
#include <cstdint>
#include <map>
namespace ndpsim {
// Exact non-reuse tombstones compressed into disjoint inclusive ranges. Sequential
// runtime-issued IDs need O(1) space; out-of-order gaps need O(active concurrency).
class CompletedIds {
    std::map<uint64_t, uint64_t> ranges_;

public:
    bool contains(uint64_t id) const {
        auto it = ranges_.upper_bound(id);
        return it != ranges_.begin() && std::prev(it)->second >= id;
    }
    bool insert(uint64_t id) {
        if (contains(id))
            return false;
        auto right = ranges_.upper_bound(id);
        auto left = right;
        uint64_t a = id, b = id;
        if (left != ranges_.begin()) {
            --left;
            if (left->second != UINT64_MAX && left->second + 1 == id) {
                a = left->first;
                ranges_.erase(left);
            }
        }
        if (right != ranges_.end() && id != UINT64_MAX && right->first == id + 1) {
            b = right->second;
            ranges_.erase(right);
        }
        ranges_.emplace(a, b);
        return true;
    }
    size_t size() const {
        return ranges_.size();
    }
};
} // namespace ndpsim
