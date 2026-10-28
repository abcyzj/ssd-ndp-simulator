#pragma once
#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

namespace ndpsim::content {
using Bytes = std::vector<uint8_t>;
enum class Code { NotReady, InvalidHandle, Released, WrongLocation, PermissionDenied,
    VersionMismatch, OutOfRange, Overflow, InvalidArgument, Unsupported, DependencyFailed, BackendFailure, CapacityExceeded };
const char* name(Code);
struct Context { uint64_t query=0, task=0, time_ns=0; };
struct Error : std::runtime_error {
    Code code; Context context; uint64_t handle=0, request=0;
    Error(Code, std::string, Context={}, uint64_t handle=0, uint64_t request=0);
};
enum class Kind { Query, Vector, Adjacency };
struct ObjectRef {
    uint64_t dataset; Kind kind; uint32_t id; uint64_t version;
    auto key() const { return std::make_tuple(dataset,kind,id); }
    bool operator==(const ObjectRef& b) const { return key()==b.key() && version==b.version; }
};
struct Range { uint64_t offset, length; };
struct Selection { ObjectRef object; Range range; };
struct Input { ObjectRef object; Bytes bytes; };
struct Metadata { ObjectRef object; uint64_t size; std::string sha256; };
std::string sha256(const Bytes&);
class Handles;
// Owned immutable bytes. Only the trusted handle registry can obtain content.
class ContentStore {
public:
    explicit ContentStore(std::vector<Input>);
    ContentStore(const ContentStore&)=delete;
    ContentStore& operator=(const ContentStore&)=delete;
    Metadata metadata(ObjectRef) const;
    void validate(const Selection&) const;
private:
    struct Object { ObjectRef ref; Bytes bytes; std::string hash; };
    std::map<std::tuple<uint64_t,Kind,uint32_t>,Object> objects_;
    const Object& find(ObjectRef) const;
    Bytes read(const Selection&) const;
    friend class Handles;
};
struct Placement { ObjectRef object; uint64_t byte_offset; };
struct Slice { Selection selection; uint64_t extent_offset; };
struct Extent { uint64_t lba; uint32_t sectors; std::vector<Slice> slices; };
struct Plan { std::vector<Extent> extents; uint64_t useful_bytes=0, sector_bytes=0; };
// Namespace size is a count of 512-byte sectors, not an inclusive last LBA.
class Layout {
public:
    Layout(const ContentStore&, uint64_t namespace_sectors, std::vector<Placement>);
    Plan resolve(const std::vector<Selection>&) const;
private:
    const ContentStore& store_; uint64_t sectors_;
    std::map<std::tuple<uint64_t,Kind,uint32_t>,Placement> placements_;
};
struct AnnLayout {
    uint64_t vector_base, vector_stride, adjacency_base, adjacency_stride, end_lba;
    std::vector<Placement> placements;
};
// Deterministic sector-aligned independent regions, indexed by uint32 object ID.
AnnLayout ann_layout(const ContentStore&, uint64_t dataset, uint64_t version,
                     uint32_t count, uint32_t dimensions, uint32_t max_degree,
                     uint64_t start_lba=0);
enum class Location { SSDStorage, DeviceBuffer, HostMemory };
enum class State { Pending, Visible, Failed, Released };
enum class Consumer { Algorithm, HostExecutor, ControllerExecutor };
class Handle {
public:
    Handle()=default;
    uint64_t id() const { return id_; }
private:
    uint64_t id_=0, lease_=0;
    Handle(uint64_t id,uint64_t lease):id_(id),lease_(lease) {}
    friend class Handles;
};
struct HandleInfo {
    uint64_t id; std::vector<Selection> selections; Location location; Context producer;
    State state; uint64_t owners, in_flight;
};
struct Release { uint64_t handle, lease, owners, in_flight; bool reclaimed; };
// Trusted runtime-side owner. Algorithms receive Handle values and try_get only.
// publish() is a primitive: M2.2 must call it only after all simulated boundaries.
class Handles {
public:
    explicit Handles(const ContentStore& store):store_(store) {}
    Handles(const Handles&)=delete;
    Handles& operator=(const Handles&)=delete;
    Handle create(std::vector<Selection>, Location, Context, bool initial=false);
    Handle retain(Handle);           // Independent caller ownership; copying a token is NOT retain.
    Handle pin(Handle);              // Runtime-only producer/consumer in-flight ownership.
    void release(Handle);            // One release for each ownership token.
    HandleInfo info(Handle) const;
    void publish(Handle);
    void fail(Handle, Code root, std::string detail);
    std::vector<Bytes> try_get(Handle, Consumer, uint64_t expected_version) const;
    const std::vector<Release>& releases() const { return releases_; }
    size_t live_records() const { return records_.size(); }
private:
    struct Record { HandleInfo info; Code failure=Code::DependencyFailed; std::string detail; };
    struct Lease { uint64_t id; bool internal, active=true; };
    const ContentStore& store_;
    std::map<uint64_t,Record> records_;
    std::map<uint64_t,Lease> leases_; // Tombstones diagnose repeated release without ID reuse.
    std::vector<Release> releases_;
    Record& checked(Handle);
    const Record& checked(Handle) const;
    Handle acquire(Handle,bool internal);
};
// Algorithm-facing capability has no consumer selector or publication methods.
class AlgorithmView {
public:
    explicit AlgorithmView(const Handles& handles):handles_(handles) {}
    std::vector<Bytes> try_get(Handle h,uint64_t version) const {
        return handles_.try_get(h,Consumer::Algorithm,version);
    }
private:
    const Handles& handles_;
};
enum class Route { Ordinary, Local };
// Values only; no backend pointers or performance charge. Bind actual backend IDs,
// validate addresses and direction on completion, reject duplicates/unknown IDs.
class ReadAssociation {
public:
    ReadAssociation(const Layout&, std::vector<Selection>, Route, Location, Context);
    const Plan& plan() const { return plan_; }
    void bind(size_t extent_index,uint64_t request_id);
    void complete(uint64_t request_id,uint64_t lba,uint32_t sectors,bool write);
    bool complete() const;
private:
    Plan plan_; Context context_;
    struct Request { size_t extent; bool done=false; };
    std::map<uint64_t,Request> requests_;
};
}
