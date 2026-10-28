#include "content/content.h"
#include <cstdio>
#include <functional>
using namespace ndpsim::content;
namespace {
unsigned checks=0;
void check(bool value,const char* detail) { if(!value) throw std::logic_error(detail);++checks; }
void error(Code code,std::function<void()> f) {
    try {f();} catch(const Error& e) {check(e.code==code,"wrong structured code");std::printf("REJECT %s\n",name(e.code));return;}
    throw std::logic_error("missing rejection");
}
Bytes pattern(size_t n) { Bytes b(n);for(size_t i=0;i<n;++i)b[i]=uint8_t(i%251);return b; }
}
int main() {
 try {
    ObjectRef v{1,Kind::Vector,0,1},w{1,Kind::Vector,1,1},a{1,Kind::Adjacency,0,1},q{1,Kind::Query,0,1};
    auto original=pattern(1040);
    ContentStore store({{v,original},{w,pattern(40)},{a,pattern(12)},{q,pattern(8)}});
    original[0]=255;
    check(sha256({})=="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","SHA empty");
    check(sha256({'a','b','c'})=="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","SHA abc");
    check(sha256(Bytes(1000000,'a'))=="cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0","SHA million a");
    std::printf("HASH %s\n",store.metadata(v).sha256.c_str());
    Layout layout(store,512,{{v,7*512},{w,9*512+16},{a,32*512}});
    std::vector<Selection> selections{{v,{500,540}},{w,{0,40}},{a,{0,12}},{v,{510,4}}};
    auto plan=layout.resolve(selections);
    check(plan.useful_bytes==596 && plan.sector_bytes==2048 && plan.extents.size()==2,"manual byte ledger");
    for(const auto& e:plan.extents) {
        std::printf("EXTENT %llu %u\n",e.lba,e.sectors);
        for(const auto& s:e.slices) std::printf("SLICE %u %u %llu %llu %llu\n",unsigned(s.selection.object.kind),s.selection.object.id,s.selection.range.offset,s.selection.range.length,s.extent_offset);
    }
    check(plan.extents[0].lba==7 && plan.extents[0].sectors==3 && plan.extents[1].lba==32,"sector coverage");
    check(layout.resolve({{v,{0,512}}}).sector_bytes==512,"exact sector edge");
    check(layout.resolve({{v,{511,2}}}).sector_bytes==1024,"cross sector");
    check(layout.resolve({{v,{1039,1}}}).extents[0].lba==9,"last object byte");
    check(layout.resolve({{v,{0,1040}}}).sector_bytes==1536,"padding counted");
    check(layout.resolve({{v,{0,512}},{v,{512,512}}}).extents.size()==1,"adjacent sectors merged");
    error(Code::InvalidArgument,[&]{layout.resolve({});});
    error(Code::InvalidArgument,[&]{layout.resolve({{v,{0,0}}});});
    error(Code::OutOfRange,[&]{layout.resolve({{v,{1040,1}}});});
    error(Code::Overflow,[&]{layout.resolve({{v,{UINT64_MAX,2}}});});
    auto bad=v;bad.version=2;error(Code::VersionMismatch,[&]{layout.resolve({{bad,{0,1}}});});
    bad=v;bad.id=90;error(Code::OutOfRange,[&]{layout.resolve({{bad,{0,1}}});});
    error(Code::OutOfRange,[&]{layout.resolve({{q,{0,1}}});});
    error(Code::OutOfRange,[&]{Layout x(store,8,{{v,7*512}});});
    error(Code::Overflow,[&]{Layout x(store,UINT64_MAX,{});});
    error(Code::Overflow,[&]{Layout x(store,512,{{v,UINT64_MAX-2}});});
    error(Code::InvalidArgument,[&]{Layout x(store,512,{{v,0},{w,512}});});
    error(Code::InvalidArgument,[&]{Layout x(store,512,{{v,0},{v,4096}});});
    error(Code::VersionMismatch,[&]{ContentStore x({{v,{1}},{v,{2}}});});
    error(Code::InvalidArgument,[&]{ContentStore x({{v,{1}},{v,{1}}});});
    // Backend request-size bound; split logical slices rather than truncate.
    ContentStore large({{v,Bytes(65536ULL*512)}});
    Layout big(large,65536,{{v,0}});
    auto split=big.resolve({{v,{0,65536ULL*512}}});
    check(split.extents.size()==2 && split.extents[0].sectors==65535 && split.extents[1].lba==65535 && split.extents[1].sectors==1,"request length split");
    check(split.extents[1].slices[0].selection.range.offset==65535ULL*512 && split.extents[1].slices[0].extent_offset==0,"split slice coverage");
    ContentStore smoke({{v,Bytes(128)},{w,Bytes(128)},{a,Bytes(12)},{{1,Kind::Adjacency,1,1},Bytes(68)}});
    auto defaults=ann_layout(smoke,1,1,2,32,16,7);
    check(defaults.vector_base==3584 && defaults.vector_stride==512 && defaults.adjacency_base==4608 && defaults.adjacency_stride==512 && defaults.end_lba==11,"default independent regions");
    std::printf("DEFAULT %llu %llu %llu %llu %llu\n",defaults.vector_base,defaults.vector_stride,defaults.adjacency_base,defaults.adjacency_stride,defaults.end_lba);
    Layout default_layout(smoke,11,defaults.placements);
    check(default_layout.resolve({{w,{0,128}}}).extents[0].lba==8,"default vector ID mapping");
    check(default_layout.resolve({{a,{0,12}}}).extents[0].lba==9,"default adjacency region");
    error(Code::InvalidArgument,[&]{ann_layout(smoke,1,1,2,31,16);});
    error(Code::InvalidArgument,[&]{ann_layout(smoke,1,1,2,32,0);});
    error(Code::Overflow,[&]{ann_layout(smoke,1,1,2,32,16,UINT64_MAX);});
    Handles handles(store); Context ctx{9,17,42};
    AlgorithmView algorithm(handles);
    auto disk=handles.create({{v,{0,1040}}},Location::SSDStorage,ctx,true);
    auto host=handles.create({{v,{500,540}}},Location::HostMemory,ctx);
    auto device=handles.create({{v,{500,540}}},Location::DeviceBuffer,ctx);
    auto query=handles.create({{q,{0,8}}},Location::HostMemory,ctx,true);
    check(handles.try_get(query,Consumer::Algorithm,1)[0]==pattern(8),"initial query");
    error(Code::PermissionDenied,[&]{handles.try_get(disk,Consumer::Algorithm,1);});
    error(Code::NotReady,[&]{handles.try_get(host,Consumer::Algorithm,1);});
    error(Code::VersionMismatch,[&]{handles.try_get(host,Consumer::Algorithm,2);});
    error(Code::PermissionDenied,[&]{handles.try_get(device,Consumer::Algorithm,1);});
    error(Code::WrongLocation,[&]{handles.try_get(device,Consumer::HostExecutor,1);});
    error(Code::PermissionDenied,[&]{algorithm.try_get(device,1);});
    handles.publish(device);
    check(handles.info(host).state==State::Pending,"device copy does not validate host");
    Layout relocated(store,512,{{v,100*512},{w,200*512},{a,300*512}});
    check(relocated.resolve({{v,{500,540}}}).extents[0].lba==100 && store.metadata(v).sha256==sha256(pattern(1040)),"content independent from logical relocation");
    auto data=handles.try_get(device,Consumer::ControllerExecutor,1);
    auto expected=pattern(1040);expected=Bytes(expected.begin()+500,expected.end());
    check(data[0]==expected,"real range content");
    data[0][0]=255;
    check(handles.try_get(device,Consumer::ControllerExecutor,1)[0]==expected,"read copies immutable content");
    handles.publish(host);
    check(handles.try_get(host,Consumer::Algorithm,1)[0]==expected,"host copy visible");
    auto owner=handles.retain(host),pin=handles.pin(host),alias=host;
    handles.release(host);
    error(Code::Released,[&]{handles.release(alias);});
    error(Code::Released,[&]{handles.try_get(alias,Consumer::Algorithm,2);});
    check(handles.info(pin).owners==1 && handles.info(pin).in_flight==1,"independent ownership counts");
    handles.release(owner);
    check(handles.try_get(pin,Consumer::HostExecutor,1)[0]==expected,"in-flight keeps object alive");
    handles.release(pin);
    check(handles.releases().back().reclaimed,"last access reclaims");
    error(Code::Released,[&]{handles.retain(pin);});
    error(Code::InvalidHandle,[&]{handles.info(Handle{});});
    Handles foreign(store);
    error(Code::InvalidHandle,[&]{foreign.info(device);});
    auto failed=handles.create({{v,{0,1}}},Location::HostMemory,ctx);
    handles.fail(failed,Code::BackendFailure,"root request failed");
    error(Code::VersionMismatch,[&]{handles.try_get(failed,Consumer::Algorithm,2);});
    try {handles.try_get(failed,Consumer::Algorithm,1);throw std::logic_error("missing failure");}
    catch(const Error& e) {check(e.code==Code::BackendFailure && e.context.task==17 && e.context.query==9 && e.context.time_ns==42 && e.handle==failed.id(),"failure context/root preserved");}
    error(Code::InvalidArgument,[&]{handles.publish(failed);});
    error(Code::InvalidArgument,[&]{handles.publish(device);});
    error(Code::InvalidArgument,[&]{handles.create({{v,{0,1}}},Location::HostMemory,ctx,true);});
    error(Code::Unsupported,[&]{handles.create({{v,{0,1}}},Location::SSDStorage,ctx);});
    auto pending_owner=handles.create({{v,{0,1}}},Location::HostMemory,ctx);
    auto producer_pin=handles.pin(pending_owner);
    handles.release(pending_owner);
    check(handles.info(producer_pin).state==State::Pending && handles.info(producer_pin).owners==0,"producer survives caller release before completion");
    handles.publish(producer_pin);
    check(handles.try_get(producer_pin,Consumer::HostExecutor,1)[0]==Bytes{0},"producer can finish after caller release");
    handles.release(producer_pin);
    for(auto h:{disk,device,query,failed}) handles.release(h);
    check(handles.live_records()==0,"all handle records reclaimed");
    for(const auto& r:handles.releases()) std::printf("RELEASE %llu %llu %llu %llu %u\n",r.handle,r.lease,r.owners,r.in_flight,r.reclaimed);
    ReadAssociation association(layout,selections,Route::Ordinary,Location::HostMemory,ctx);
    error(Code::WrongLocation,[&]{ReadAssociation x(layout,selections,Route::Local,Location::HostMemory,ctx);});
    error(Code::Unsupported,[&]{ReadAssociation x(layout,selections,Route::Local,Location::SSDStorage,ctx);});
    association.bind(0,101);association.bind(1,102);
    error(Code::InvalidArgument,[&]{association.bind(1,103);});
    error(Code::BackendFailure,[&]{association.complete(99,7,3,false);});
    error(Code::BackendFailure,[&]{association.complete(101,8,3,false);});
    error(Code::BackendFailure,[&]{association.complete(101,7,2,false);});
    error(Code::BackendFailure,[&]{association.complete(101,7,3,true);});
    association.complete(102,32,1,false);
    check(!association.complete(),"all requests required");
    association.complete(101,7,3,false);
    check(association.complete(),"reverse complete aggregation");
    error(Code::BackendFailure,[&]{association.complete(101,7,3,false);});
    std::printf("M2.1 PASS checks=%u live=%zu\n",checks,handles.live_records());
 } catch(const std::exception& e) {std::fprintf(stderr,"%s\n",e.what());return 1;}
}
