#include "layout/layout.h"
#include "serialization/json_schema.h"
#include <algorithm>
#include <fstream>
#include <limits>
#include <set>

namespace ndpsim::layout {
namespace {
using J = serialization::Json;
void need(bool b, const std::string& s) {
    if (!b)
        throw Error("layout: " + s);
}
uint64_t add(uint64_t a, uint64_t b) {
    need(b <= UINT64_MAX - a, "addition overflow");
    return a + b;
}
uint64_t mul(uint64_t a, uint64_t b) {
    need(!b || a <= UINT64_MAX / b, "multiplication overflow");
    return a * b;
}
void fields(const J& j, std::initializer_list<const char*> keys) {
    need(j.is_object() && j.size() == keys.size(), "missing/unknown fields");
    for (auto k : keys)
        need(j.contains(k), std::string("missing field ") + k);
}
uint64_t number(const J& j) {
    need(j.is_number_unsigned() || (j.is_number_integer() && j.get<int64_t>() >= 0),
         "expected uint64 integer");
    return j.get<uint64_t>();
}
std::string name(const J& j) {
    need(j.is_string(), "expected name");
    auto s = j.get<std::string>();
    need(!s.empty() && s.size() <= 256, "name length");
    return s;
}
bool power2(uint64_t n) {
    return n && !(n & (n - 1));
}
} // namespace
Layout::Layout(const std::filesystem::path& reference,
               const std::filesystem::path& declaring,
               const std::vector<capture::Object>& objects,
               Limits limits)
    : limits_(limits) {
    need(declaring.is_absolute(), "declaring file must be absolute (no cwd fallback)");
    need(!reference.empty(), "empty file reference");
    path_ = std::filesystem::canonical(
        reference.is_absolute() ? reference : declaring.parent_path() / reference);
    need(std::filesystem::file_size(path_) <= limits.file_bytes, "file byte budget");
    std::ifstream f(path_, std::ios::binary);
    need(bool(f), "cannot open configuration");
    // Bounded read even if the file grows after file_size().
    char block[8192];
    while (f.read(block, sizeof block) || f.gcount()) {
        need(uint64_t(f.gcount()) <= limits.file_bytes - source_.size(), "file byte budget");
        source_.append(block, size_t(f.gcount()));
    }
    need(f.eof(), "configuration read failure");
    unsigned depth = 0;
    bool quoted = false, escaped = false;
    for (char c : source_) {
        if (quoted) {
            if (escaped)
                escaped = false;
            else if (c == '\\')
                escaped = true;
            else if (c == '"')
                quoted = false;
        } else if (c == '"')
            quoted = true;
        else if (c == '{' || c == '[')
            need(++depth <= 32, "JSON nesting budget");
        else if (c == '}' || c == ']') {
            need(depth > 0, "JSON nesting mismatch");
            --depth;
        }
    }
    auto j = serialization::parse_unique(source_, [](const std::string& k) {
        throw Error("layout: duplicate key " + k);
    });
    fields(j, {"format", "namespace", "alignment_bytes", "objects"});
    need(j.at("format") == "ndpsim-layout/1", "unsupported format");
    const auto& ns = j.at("namespace");
    fields(ns, {"id", "sector_bytes", "page_bytes", "capacity_sectors"});
    name(ns.at("id"));
    sector_ = number(ns.at("sector_bytes"));
    page_ = number(ns.at("page_bytes"));
    capacity_ = number(ns.at("capacity_sectors"));
    auto alignment = number(j.at("alignment_bytes"));
    need(sector_ == 512, "Unsupported: backend sector size must be 512 bytes");
    need(power2(page_) && page_ >= sector_, "page must be power of two and >= sector");
    need(power2(alignment), "alignment must be power of two");
    need(capacity_ > 0, "empty namespace");
    const auto capacity_bytes = mul(capacity_, sector_);
    need(objects.size() <= limits.objects, "object budget");
    for (const auto& o : objects) {
        need(!o.id.empty() && o.id.size() <= 256, "invalid object id");
        need(objects_.emplace(std::make_pair(o.id, o.version), Object{o.size_bytes, {}}).second,
             "duplicate metadata object");
    }
    need(j.at("objects").is_array() && j.at("objects").size() == objects_.size(),
         "object coverage");
    std::set<std::pair<std::string, uint64_t>> seen;
    std::vector<std::pair<uint64_t, uint64_t>> physical;
    uint64_t count = 0;
    for (const auto& row : j.at("objects")) {
        fields(row, {"object", "version", "extents"});
        auto key = std::make_pair(name(row.at("object")), number(row.at("version")));
        need(objects_.count(key) && seen.insert(key).second, "unknown/duplicate object or version");
        auto& o = objects_.at(key);
        const auto& es = row.at("extents");
        need(es.is_array(), "extents array");
        need(es.size() <= limits.extents - count, "extent budget");
        count += es.size();
        for (const auto& e : es) {
            fields(e, {"logical_offset_bytes", "physical_offset_bytes", "length_bytes"});
            Extent x{number(e.at("logical_offset_bytes")),
                     number(e.at("physical_offset_bytes")),
                     number(e.at("length_bytes"))};
            need(x.length > 0, "empty extent");
            need(x.physical % alignment == 0, "unaligned placement");
            need(add(x.logical, x.length) <= o.size, "logical extent out of range");
            auto end = add(x.physical, x.length);
            need(end <= capacity_bytes, "namespace capacity");
            physical.emplace_back(x.physical, end);
            o.extents.push_back(x);
        }
        std::sort(o.extents.begin(), o.extents.end(), [](auto a, auto b) {
            return a.logical < b.logical;
        });
        uint64_t end = 0;
        for (auto x : o.extents) {
            need(x.logical == end, "logical gap/overlap");
            end = add(end, x.length);
        }
        need(end == o.size, "incomplete logical coverage");
    }
    std::sort(physical.begin(), physical.end());
    for (size_t i = 1; i < physical.size(); ++i)
        need(physical[i - 1].second <= physical[i].first, "physical overlap");
    for (const auto& range : physical)
        mapped_end_ = std::max(mapped_end_, range.second / sector_ + (range.second % sector_ != 0));
    hash_ = capture::sha256(source_);
    effective_ = j.dump();
}
Plan Layout::resolve(const std::vector<capture::Access>& accesses) const {
    need(accesses.size() <= limits_.accesses, "access budget");
    // One entry per touched sector, never a payload-sized placeholder. Bound it
    // before insertion. Duplicate accesses share storage reads but retain slices.
    std::map<uint64_t, std::vector<Slice>> sectors;
    Plan plan;
    uint64_t fragments = 0;
    for (size_t i = 0; i < accesses.size(); ++i) {
        const auto& a = accesses[i];
        auto it = objects_.find({a.object, a.version});
        need(it != objects_.end(), "unknown access object/version");
        const auto& o = it->second;
        auto end = add(a.offset_bytes, a.length_bytes);
        need(end <= o.size, "access out of bounds");
        plan.logical_bytes = add(plan.logical_bytes, a.length_bytes);
        if (!a.length_bytes)
            continue;
        auto first = std::lower_bound(o.extents.begin(),
                                      o.extents.end(),
                                      a.offset_bytes,
                                      [](const Extent& x, uint64_t offset) {
                                          return x.logical + x.length <= offset;
                                      });
        for (auto e = first; e != o.extents.end() && e->logical < end; ++e) {
            const auto x = *e;
            auto lo = std::max(a.offset_bytes, x.logical), hi = std::min(end, x.logical + x.length);
            if (lo >= hi)
                continue;
            auto start = add(x.physical, lo - x.logical), stop = add(start, hi - lo);
            while (start < stop) {
                auto lba = start / sector_;
                auto n = std::min(stop - start, sector_ - start % sector_);
                need(++fragments <= limits_.plan_sectors, "plan fragment budget");
                sectors[lba].push_back({i, lo, start % sector_, n});
                lo += n;
                start += n;
            }
        }
    }
    const auto per_page = page_ / sector_;
    for (const auto& entry : sectors) {
        auto lba = entry.first;
        if (plan.reads.empty() || plan.reads.back().lba + plan.reads.back().sectors != lba ||
            plan.reads.back().lba / per_page != lba / per_page)
            plan.reads.push_back({lba, 0, {}});
        auto& r = plan.reads.back();
        ++r.sectors;
        for (auto s : entry.second) {
            s.request_offset_bytes = add(mul(lba - r.lba, sector_), s.request_offset_bytes);
            r.slices.push_back(s);
        }
        plan.sector_bytes = add(plan.sector_bytes, sector_);
    }
    return plan;
}
} // namespace ndpsim::layout
