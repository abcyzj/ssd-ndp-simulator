#include "ordinary_fixture.h"
#include "content/content.h"
#include "ssd/NVM_PHY_ONFI.h"
#include <set>
using namespace ndpsim::content;
namespace {
std::multiset<std::pair<uint64_t,unsigned>> pages;
std::multiset<std::tuple<uint64_t,unsigned,uint64_t,unsigned>> request_pages;
void transaction(SSD_Components::NVM_Transaction_Flash* t) {
    if(t->Source!=SSD_Components::Transaction_Source_Type::USERIO || t->Type!=SSD_Components::Transaction_Type::READ) return;
    pages.emplace(t->LPA,t->Data_and_metadata_size_in_byte);
    std::printf("PAGE %llu %u\n",t->LPA,t->Data_and_metadata_size_in_byte);
    if(!t->UserIORequest) throw std::logic_error("user transaction missing parent request");
    auto* r=t->UserIORequest;
    request_pages.emplace(r->Start_LBA,r->SizeInSectors,t->LPA,t->Data_and_metadata_size_in_byte);
    std::printf("PAGE_REQUEST %llu %u %llu %u\n",r->Start_LBA,r->SizeInSectors,t->LPA,t->Data_and_metadata_size_in_byte);
}
}
int main() {
 try {
    ObjectRef v{1,Kind::Vector,0,1},w{1,Kind::Vector,1,1},a{1,Kind::Adjacency,0,1};
    Bytes bytes(1040);for(size_t i=0;i<bytes.size();++i)bytes[i]=uint8_t(i%251);
    Bytes wbytes(40),abytes(12);
    for(size_t i=0;i<wbytes.size();++i)wbytes[i]=uint8_t(i%251);
    for(size_t i=0;i<abytes.size();++i)abytes[i]=uint8_t(i%251);
    ContentStore store({{v,bytes},{w,wbytes},{a,abytes}});
    Layout layout(store,512,{{v,7*512},{w,9*512+16},{a,32*512}});
    std::vector<Selection> selections{{v,{500,540}},{w,{0,40}},{a,{0,12}},{v,{510,4}}};
    ReadAssociation read(layout,selections,Route::Ordinary,Location::HostMemory,{9,17,0});
    Handles handles(store);
    auto h=handles.create(selections,Location::HostMemory,{9,17,0});
    OrdinaryFixture fixture;
    // Register before Setup_triggers installs cache callbacks: the cache may free
    // the final transaction's parent request during its serviced callback.
    static_cast<SSD_Components::NVM_PHY_ONFI*>(fixture.device->PHY)->ConnectToTransactionServicedSignal(transaction);
    fixture.start();
    unsigned steps=0;
    while(!fixture.window->advance(!Simulator->Peek_next_time(),Simulator->Time())) {
        auto next=Simulator->Peek_next_time();
        if(!next || ++steps>100000) throw std::logic_error("preload stalled");
        Simulator->Dispatch_current_time(*next);
    }
    auto old=fixture.flow->completed;
    fixture.flow->completed=[&](ndpsim::ExternalFlow::Completion c) {
        old(c);read.complete(c.token,c.lba,c.sectors,c.write);
        std::printf("ASSOCIATED %llu %llu %u %llu\n",c.token,c.lba,c.sectors,c.time);
        if(read.complete()) handles.publish(h); // Probe only: real M2.2 will enqueue Publish phase.
    };
    for(size_t i=0;i<read.plan().extents.size();++i) {
        auto& e=read.plan().extents[i];auto id=fixture.window->read(e.lba,e.sectors);read.bind(i,id);
        std::printf("BOUND %llu %llu %u query=9 task=17\n",id,e.lba,e.sectors);
    }
    bool pending=false;
    try {handles.try_get(h,Consumer::Algorithm,1);} catch(const Error& e) {pending=e.code==Code::NotReady;}
    if(!pending) throw std::logic_error("early numeric view");
    while(auto next=Simulator->Peek_next_time()) {
        if(++steps>100000) throw std::logic_error("read stalled");Simulator->Dispatch_current_time(*next);
    }
    if(!read.complete() || fixture.flow->pending()) throw std::logic_error("unassociated request");
    auto data=handles.try_get(h,Consumer::Algorithm,1);
    if(data.size()!=4 || data[0]!=Bytes(bytes.begin()+500,bytes.end()) || data[1]!=wbytes || data[2]!=abytes || data[3]!=Bytes(bytes.begin()+510,bytes.begin()+514))
        throw std::logic_error("backend/content range mismatch");
    if(pages!=std::multiset<std::pair<uint64_t,unsigned>>{{0,512},{1,1024},{4,512}})
        throw std::logic_error("actual LPA/page split differs from content extents");
    if(request_pages!=std::multiset<std::tuple<uint64_t,unsigned,uint64_t,unsigned>>{{7,3,0,512},{7,3,1,1024},{32,1,4,512}})
        throw std::logic_error("LPA metadata does not match parent request extent");
    for(size_t i=0;i<data.size();++i) std::printf("VALUE %zu %zu %s\n",i,data[i].size(),sha256(data[i]).c_str());
    handles.release(h);
    std::printf("M2.1 BACKEND PASS extents=2 useful=596 sector=2048 page_transactions=%zu live=%zu\n",
        pages.size(),handles.live_records());
 } catch(const std::exception& e) {std::fprintf(stderr,"%s\n",e.what());return 1;}
}
