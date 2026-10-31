#include "content/content.h"
#include <algorithm>
#include <limits>
#include <set>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <cmath>
#include <cstring>
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
namespace {
uint64_t add(uint64_t a, uint64_t b) {
    if (b > UINT64_MAX - a)
        throw Error(Code::Overflow, "uint64 addition");
    return a + b;
}
uint64_t mul(uint64_t a, uint64_t b) {
    if (a && b > UINT64_MAX / a)
        throw Error(Code::Overflow, "uint64 multiplication");
    return a * b;
}
uint64_t next_id() {
    // Frozen v1: single process/rank/thread; never reuse IDs, including across registries.
    static uint64_t id = 0;
    if (id == UINT64_MAX)
        throw Error(Code::Overflow, "stable ID exhausted");
    return ++id;
}
} // namespace
struct ContentStore::Files {
    struct Mapping {
        std::string path, hash;
        int fd = -1;
        const uint8_t* data = nullptr;
        uint64_t size = 0;
        struct stat original{};
        Mapping(std::string p, uint64_t expected) : path(std::move(p)) {
            fd = open(path.c_str(), O_RDONLY);
            if (fd < 0)
                throw Error(Code::InvalidArgument, "cannot open " + path);
            if (fstat(fd, &original) || !S_ISREG(original.st_mode) ||
                uint64_t(original.st_size) != expected || !expected || expected > SIZE_MAX) {
                close(fd);
                fd = -1;
                throw Error(Code::InvalidArgument, "bad mapped file size/type " + path);
            }
            size = expected;
            auto ptr = mmap(nullptr, size, PROT_READ, MAP_PRIVATE, fd, 0);
            if (ptr == MAP_FAILED) {
                close(fd);
                fd = -1;
                throw Error(Code::BackendFailure, "mmap " + path);
            }
            data = static_cast<const uint8_t*>(ptr);
            hash = sha256(data, size);
        }
        ~Mapping() {
            if (data)
                munmap(const_cast<uint8_t*>(data), size);
            if (fd >= 0)
                close(fd);
        }
        void verify() const {
            struct stat current{}, named{};
#ifdef __APPLE__
            auto changed = [](const struct stat& a, const struct stat& b) {
                return a.st_mtimespec.tv_sec != b.st_mtimespec.tv_sec ||
                       a.st_mtimespec.tv_nsec != b.st_mtimespec.tv_nsec ||
                       a.st_ctimespec.tv_sec != b.st_ctimespec.tv_sec ||
                       a.st_ctimespec.tv_nsec != b.st_ctimespec.tv_nsec;
            };
#else
            auto changed = [](const struct stat& a, const struct stat& b) {
                return a.st_mtim.tv_sec != b.st_mtim.tv_sec ||
                       a.st_mtim.tv_nsec != b.st_mtim.tv_nsec ||
                       a.st_ctim.tv_sec != b.st_ctim.tv_sec ||
                       a.st_ctim.tv_nsec != b.st_ctim.tv_nsec;
            };
#endif
            if (fstat(fd, &current) || stat(path.c_str(), &named) || changed(current, original) ||
                current.st_size != original.st_size || named.st_dev != original.st_dev ||
                named.st_ino != original.st_ino || named.st_size != original.st_size ||
                sha256(data, size) != hash)
                throw Error(Code::VersionMismatch, "mapped input changed " + path);
        }
    };
    uint32_t count, dimensions, degree;
    Mapping vectors, adjacency;
    Files(std::string v, std::string a, uint32_t n, uint32_t d, uint32_t r)
        : count(n), dimensions(d), degree(r), vectors(std::move(v), mul(mul(n, d), 4)),
          adjacency(std::move(a), mul(mul(n, add(r, 1)), 4)) {
    }
};
ContentStore::~ContentStore() = default;
ContentStore::ContentStore(std::string v, std::string a, uint32_t n, uint32_t d, uint32_t r) {
    if (!n || !d || r >= n)
        throw Error(Code::InvalidArgument, "mapped shape");
    files_ = std::make_unique<Files>(std::move(v), std::move(a), n, d, r);
    for (uint32_t id = 0; id < n; ++id) {
        auto vector = file_object({1, Kind::Vector, id, 1});
        for (uint64_t j = 0; j < vector.second; j += 4) {
            uint32_t bits = 0;
            for (unsigned k = 0; k < 4; ++k)
                bits |= uint32_t(vector.first[j + k]) << (8 * k);
            float x;
            std::memcpy(&x, &bits, 4);
            if (!std::isfinite(x))
                throw Error(Code::InvalidArgument, "nonfinite mapped FP32");
        }
        auto adjacency = file_object({1, Kind::Adjacency, id, 1});
        for (uint64_t j = 4; j < adjacency.second; j += 4) {
            uint32_t neighbor = 0;
            for (unsigned k = 0; k < 4; ++k)
                neighbor |= uint32_t(adjacency.first[j + k]) << (8 * k);
            if (neighbor >= n)
                throw Error(Code::OutOfRange, "mapped graph ID");
        }
    }
    verify_identity();
}
std::pair<const uint8_t*, uint64_t> ContentStore::file_object(ObjectRef ref) const {
    if (ref.dataset != 1 || ref.version != 1)
        throw Error(Code::VersionMismatch, "mapped identity");
    if (ref.id >= files_->count)
        throw Error(Code::OutOfRange, "mapped ID");
    if (ref.kind == Kind::Vector) {
        uint64_t size = uint64_t(files_->dimensions) * 4;
        return {files_->vectors.data + size * ref.id, size};
    }
    if (ref.kind != Kind::Adjacency)
        throw Error(Code::OutOfRange, "mapped kind");
    auto data = files_->adjacency.data + (uint64_t(files_->degree) + 1) * 4 * ref.id;
    uint32_t degree = 0;
    for (unsigned k = 0; k < 4; ++k)
        degree |= uint32_t(data[k]) << (8 * k);
    if (degree > files_->degree)
        throw Error(Code::OutOfRange, "mapped degree");
    return {data, (uint64_t(degree) + 1) * 4};
}
void ContentStore::verify_identity() const {
    if (files_) {
        files_->vectors.verify();
        files_->adjacency.verify();
    }
}
ContentStore::ContentStore(std::vector<Input> inputs) {
    for (auto& input : inputs) {
        auto ref = input.object;
        if (!ref.dataset || !ref.version || input.bytes.empty())
            throw Error(Code::InvalidArgument, "empty content or zero dataset/version");
        auto hash = sha256(input.bytes);
        auto it = objects_.find(ref.key());
        if (it != objects_.end()) {
            if (it->second.ref.version != ref.version || it->second.hash != hash)
                throw Error(Code::VersionMismatch, "conflicting immutable object/version/hash");
            throw Error(Code::InvalidArgument, "duplicate object");
        }
        objects_.emplace(ref.key(), Object{ref, std::move(input.bytes), std::move(hash)});
    }
}
const ContentStore::Object& ContentStore::find(ObjectRef ref) const {
    auto it = objects_.find(ref.key());
    if (it == objects_.end())
        throw Error(Code::OutOfRange, "unknown object");
    if (it->second.ref.version != ref.version)
        throw Error(Code::VersionMismatch, "object version");
    return it->second;
}
Metadata ContentStore::metadata(ObjectRef ref) const {
    if (files_) {
        auto obj = file_object(ref);
        return {ref, obj.second, sha256(obj.first, obj.second)};
    }
    const auto& obj = find(ref);
    return {ref, obj.bytes.size(), obj.hash};
}
void ContentStore::validate(const Selection& s) const {
    auto size = files_ ? file_object(s.object).second : find(s.object).bytes.size();
    if (!s.range.length)
        throw Error(Code::InvalidArgument, "zero byte range");
    auto end = add(s.range.offset, s.range.length);
    if (end > size)
        throw Error(Code::OutOfRange, "object byte range");
}
Bytes ContentStore::read(const Selection& s) const {
    validate(s);
    if (files_) {
        auto obj = file_object(s.object);
        return Bytes(obj.first + s.range.offset, obj.first + s.range.offset + s.range.length);
    }
    const auto& data = find(s.object).bytes;
    return Bytes(data.begin() + s.range.offset, data.begin() + s.range.offset + s.range.length);
}
Layout::Layout(const ContentStore& store, uint64_t sectors, std::vector<Placement> placements)
    : store_(store), sectors_(sectors) {
    if (!sectors_)
        throw Error(Code::InvalidArgument, "empty namespace");
    auto capacity = mul(sectors, 512);
    std::vector<std::pair<uint64_t, uint64_t>> occupied;
    for (auto p : placements) {
        auto size = store.metadata(p.object).size;
        auto end = add(p.byte_offset, size);
        if (end > capacity)
            throw Error(Code::OutOfRange, "placement outside namespace");
        if (!placements_.emplace(p.object.key(), p).second)
            throw Error(Code::InvalidArgument, "duplicate placement");
        occupied.emplace_back(p.byte_offset, end);
    }
    std::sort(occupied.begin(), occupied.end());
    for (size_t i = 1; i < occupied.size(); ++i)
        if (occupied[i].first < occupied[i - 1].second)
            throw Error(Code::InvalidArgument, "overlapping object bytes");
}
Layout::Layout(const ContentStore& store, uint64_t sectors, const AnnLayout& ann, uint32_t count)
    : store_(store), sectors_(sectors), vector_base_(ann.vector_base),
      vector_stride_(ann.vector_stride), adjacency_base_(ann.adjacency_base),
      adjacency_stride_(ann.adjacency_stride), count_(count) {
    if (!count || !sectors || ann.end_lba > sectors ||
        ann.adjacency_base != add(ann.vector_base, mul(count, ann.vector_stride)) ||
        mul(ann.end_lba, 512) != add(ann.adjacency_base, mul(count, ann.adjacency_stride)))
        throw Error(Code::OutOfRange, "formula layout namespace/shape");
}
Plan Layout::resolve(const std::vector<Selection>& selections) const {
    if (selections.empty())
        throw Error(Code::InvalidArgument, "empty read batch");
    struct Span {
        uint64_t first, end, start;
        Selection s;
    };
    std::vector<Span> spans;
    Plan plan;
    for (auto s : selections) {
        store_.validate(s);
        uint64_t position;
        if (count_) {
            if (s.object.dataset != 1 || s.object.version != 1 || s.object.id >= count_ ||
                (s.object.kind != Kind::Vector && s.object.kind != Kind::Adjacency))
                throw Error(Code::OutOfRange, "formula layout object");
            bool vector = s.object.kind == Kind::Vector;
            auto stride = vector ? vector_stride_ : adjacency_stride_;
            if (store_.metadata(s.object).size > stride)
                throw Error(Code::OutOfRange, "object exceeds formula stride");
            position = add(vector ? vector_base_ : adjacency_base_, mul(s.object.id, stride));
        } else {
            auto it = placements_.find(s.object.key());
            if (it == placements_.end())
                throw Error(Code::OutOfRange, "object has no SSD placement");
            position = it->second.byte_offset;
        }
        auto start = add(position, s.range.offset), end = add(start, s.range.length);
        auto first = start / 512, last = (end - 1) / 512 + 1;
        if (last > sectors_)
            throw Error(Code::OutOfRange, "resolved namespace extent");
        spans.push_back({first, last, start, s});
        plan.useful_bytes = add(plan.useful_bytes, s.range.length);
    }
    std::sort(spans.begin(), spans.end(), [](const auto& a, const auto& b) {
        return a.first < b.first;
    });
    // Merge first, then split only for the ordinary backend's 65535-sector limit.
    std::vector<std::pair<uint64_t, uint64_t>> merged;
    for (auto s : spans) {
        if (merged.empty() || s.first > merged.back().second)
            merged.emplace_back(s.first, s.end);
        else
            merged.back().second = std::max(merged.back().second, s.end);
    }
    for (auto [first, end] : merged) {
        while (first < end) {
            auto count = std::min<uint64_t>(end - first, 65535);
            Extent e{first, uint32_t(count), {}};
            auto start = mul(first, 512), limit = mul(first + count, 512);
            for (const auto& span : spans) {
                auto a = std::max(start, span.start),
                     b = std::min(limit, add(span.start, span.s.range.length));
                if (a < b) {
                    auto slice = span.s;
                    slice.range = {add(slice.range.offset, a - span.start), b - a};
                    e.slices.push_back({slice, a - start});
                }
            }
            plan.sector_bytes = add(plan.sector_bytes, mul(count, 512));
            plan.extents.push_back(std::move(e));
            first += count;
        }
    }
    return plan;
}
AnnLayout ann_layout(const ContentStore& store,
                     uint64_t dataset,
                     uint64_t version,
                     uint32_t count,
                     uint32_t dimensions,
                     uint32_t max_degree,
                     uint64_t start_lba,
                     bool compact) {
    if (!count || !dimensions)
        throw Error(Code::InvalidArgument, "nonzero count/dimension required");
    auto vector_bytes = mul(dimensions, 4), adjacency_bytes = add(4, mul(max_degree, 4));
    auto align = [](uint64_t n) {
        return mul(add(n, 511) / 512, 512);
    };
    AnnLayout out{mul(start_lba, 512), align(vector_bytes), 0, align(adjacency_bytes), 0, {}};
    out.adjacency_base = add(out.vector_base, mul(count, out.vector_stride));
    out.end_lba = add(out.adjacency_base, mul(count, out.adjacency_stride)) / 512;
    if (compact)
        return out;
    for (uint64_t id = 0; id < count; ++id) {
        ObjectRef v{dataset, Kind::Vector, uint32_t(id), version},
            a{dataset, Kind::Adjacency, uint32_t(id), version};
        if (store.metadata(v).size != vector_bytes || store.metadata(a).size < 4 ||
            store.metadata(a).size > adjacency_bytes)
            throw Error(Code::InvalidArgument, "object size differs from declared ANNS layout");
        out.placements.push_back({v, add(out.vector_base, mul(id, out.vector_stride))});
        out.placements.push_back({a, add(out.adjacency_base, mul(id, out.adjacency_stride))});
    }
    return out;
}
const Handles::Record& Handles::checked(Handle h) const {
    if (h.active_ && !*h.active_)
        throw Error(Code::Released, "ownership token released", {}, h.id_);
    auto it = leases_.find(h.lease_);
    if (!h.id_ || it == leases_.end() || it->second.id != h.id_)
        throw Error(Code::InvalidHandle, "unknown ownership token", {}, h.id_);
    return records_.at(h.id_);
}
Handles::Record& Handles::checked(Handle h) {
    return const_cast<Record&>(static_cast<const Handles&>(*this).checked(h));
}
Handle Handles::create(std::vector<Selection> selections,
                       Location location,
                       Context context,
                       bool initial) {
    if (selections.empty())
        throw Error(Code::InvalidArgument, "empty handle", context);
    uint64_t version = selections.front().object.version;
    for (auto s : selections) {
        store_.validate(s);
        if (s.object.version != version)
            throw Error(Code::VersionMismatch, "mixed handle versions", context);
    }
    if (!context.task || !context.query)
        throw Error(Code::InvalidArgument, "producer query/task required", context);
    if (initial) {
        for (auto s : selections) {
            bool query = s.object.kind == Kind::Query;
            if ((query && location != Location::HostMemory) ||
                (!query && location != Location::SSDStorage))
                throw Error(Code::InvalidArgument,
                            "initial location must be query=host, vector/adjacency=SSD",
                            context);
        }
    } else if (location == Location::SSDStorage)
        throw Error(Code::Unsupported, "read destination SSDStorage", context);
    auto id = next_id(), lease = next_id();
    records_.emplace(id,
                     Record{{id,
                             std::move(selections),
                             location,
                             context,
                             initial ? State::Visible : State::Pending,
                             1,
                             0},
                            Code::DependencyFailed,
                            {}});
    leases_.emplace(lease, Lease{id, false});
    return Handle(id, lease);
}
Handle Handles::acquire(Handle h, bool internal) {
    auto& rec = checked(h);
    auto& count = internal ? rec.info.in_flight : rec.info.owners;
    auto updated = add(count, 1), lease = next_id();
    leases_.emplace(lease, Lease{h.id_, internal});
    count = updated;
    return Handle(h.id_, lease);
}
Handle Handles::retain(Handle h) {
    return acquire(h, false);
}
Handle Handles::pin(Handle h) {
    return acquire(h, true);
}
void Handles::release(Handle h) {
    auto& rec = checked(h);
    auto& lease = leases_.at(h.lease_);
    *h.active_ = false;
    --(lease.internal ? rec.info.in_flight : rec.info.owners);
    bool reclaimed = !rec.info.owners && !rec.info.in_flight;
    releases_.push_back({h.id_, h.lease_, rec.info.owners, rec.info.in_flight, reclaimed});
    leases_.erase(h.lease_);
    if (reclaimed)
        records_.erase(h.id_);
}
HandleInfo Handles::info(Handle h) const {
    return checked(h).info;
}
void Handles::publish(Handle h) {
    auto& rec = checked(h);
    if (rec.info.state != State::Pending)
        throw Error(Code::InvalidArgument, "publish requires Pending", rec.info.producer, h.id_);
    rec.info.state = State::Visible;
}
void Handles::fail(Handle h, Code root, std::string detail) {
    auto& rec = checked(h);
    if (rec.info.state != State::Pending || root == Code::NotReady)
        throw Error(Code::InvalidArgument, "invalid failure transition", rec.info.producer, h.id_);
    rec.failure = root;
    rec.detail = std::move(detail);
    rec.info.state = State::Failed;
}
std::vector<Bytes> Handles::try_get(Handle h, Consumer consumer, uint64_t version) const {
    const auto& rec = checked(h);
    const auto& i = rec.info;
    if (i.selections.front().object.version != version)
        throw Error(Code::VersionMismatch, "handle content version", i.producer, h.id_);
    if (i.location == Location::SSDStorage)
        throw Error(Code::PermissionDenied, "SSD reference has no numeric view", i.producer, h.id_);
    if (consumer == Consumer::Algorithm && i.location != Location::HostMemory)
        throw Error(
            Code::PermissionDenied, "algorithm may read HostMemory only", i.producer, h.id_);
    if ((consumer == Consumer::HostExecutor && i.location != Location::HostMemory) ||
        (consumer == Consumer::ControllerExecutor && i.location != Location::DeviceBuffer))
        throw Error(Code::WrongLocation, "executor input location", i.producer, h.id_);
    if (i.state == State::Failed)
        throw Error(rec.failure, rec.detail, i.producer, h.id_);
    if (i.state != State::Visible)
        throw Error(Code::NotReady, "copy is Pending", i.producer, h.id_);
    std::vector<Bytes> result;
    for (auto s : i.selections)
        result.push_back(store_.read(s));
    return result;
}
ReadAssociation::ReadAssociation(const Layout& layout,
                                 std::vector<Selection> selections,
                                 Route route,
                                 Location dest,
                                 Context ctx)
    : plan_(layout.resolve(selections)), context_(ctx) {
    if (!ctx.query || !ctx.task)
        throw Error(Code::InvalidArgument, "read query/task required", ctx);
    if (dest == Location::SSDStorage)
        throw Error(Code::Unsupported, "read destination SSDStorage", ctx);
    if ((dest == Location::HostMemory && route != Route::Ordinary) ||
        (dest == Location::DeviceBuffer && route != Route::Local))
        throw Error(Code::WrongLocation, "route/destination mismatch", ctx);
}
void ReadAssociation::bind(size_t index, uint64_t id) {
    if (!id || index >= plan_.extents.size())
        throw Error(Code::InvalidArgument, "request ID or extent index", context_, 0, id);
    for (const auto& entry : requests_)
        if (entry.second.extent == index)
            throw Error(Code::InvalidArgument, "extent already bound", context_, 0, id);
    if (!requests_.emplace(id, Request{index}).second)
        throw Error(Code::InvalidArgument, "request ID already bound", context_, 0, id);
}
void ReadAssociation::complete(uint64_t id, uint64_t lba, uint32_t sectors, bool write) {
    auto it = requests_.find(id);
    if (it == requests_.end())
        throw Error(Code::BackendFailure, "unknown completion", context_, 0, id);
    const auto& e = plan_.extents.at(it->second.extent);
    if (it->second.done || write || lba != e.lba || sectors != e.sectors)
        throw Error(Code::BackendFailure,
                    "duplicate or mismatched completion extent/direction",
                    context_,
                    0,
                    id);
    it->second.done = true;
}
bool ReadAssociation::complete() const {
    return requests_.size() == plan_.extents.size() &&
           std::all_of(requests_.begin(), requests_.end(), [](const auto& p) {
               return p.second.done;
           });
}
} // namespace ndpsim::content
