#pragma once
#include "backend/local_read.h"
#include "runtime/runtime.h"
#include "operators/operators.h"
#include "transport/pcie.h"
#include "transport/conversation.h"
#include <deque>

namespace ndpsim::model {
// All clocks and callbacks belong to the SST bridge. Owners outlive queued work.
uint64_t checked_add(uint64_t,uint64_t);
uint64_t checked_mul(uint64_t,uint64_t);
struct ResourceRecord {
    uint64_t query,task,submitted,start,end,bytes;
    std::string stage;
};
class FifoResource {
public:
    FifoResource(EventBridge&,EventBridge::Clock,uint64_t slots);
    void submit(content::Context,std::string stage,uint64_t bytes,uint64_t duration,
                std::function<void()> begin,std::function<void()> done);
    const std::vector<ResourceRecord>& records() const{return records_;}
    size_t pending() const{return queue_.size()+busy_;}
private:
    struct Job {content::Context context;std::string stage;uint64_t bytes,duration,submitted;
        std::function<void()> begin,done;};
    EventBridge& bridge_;EventBridge::Clock now_;uint64_t slots_,busy_=0;
    std::deque<Job> queue_;std::vector<ResourceRecord> records_;
    void pump();
};
struct CreditRecord {uint64_t time,id,query,task,bytes,used;bool reserve;std::string lifetime;};
class BufferModel {
public:
    BufferModel(EventBridge&,EventBridge::Clock,uint64_t capacity);
    void reserve(uint64_t bytes,content::Context,std::string lifetime,std::function<void(uint64_t)>);
    void release(uint64_t id,uint64_t bytes);
    uint64_t used() const{return used_;}
    uint64_t peak() const{return peak_;}
    size_t waiting() const{return queue_.size();}
    const std::vector<CreditRecord>& records() const{return records_;}
private:
    struct Request {uint64_t id,bytes;content::Context context;std::string lifetime;std::function<void(uint64_t)> done;};
    EventBridge& bridge_;EventBridge::Clock now_;uint64_t capacity_,used_=0,peak_=0,next_=0;
    std::deque<Request> queue_;std::map<uint64_t,Request> live_;std::vector<CreditRecord> records_;
    void pump();
};
struct ControllerConfig {
    uint64_t capacity_bytes=16384, staging_bytes_per_ns=16, slots=1, launch_ns=1, flops_per_ns=16;
    uint64_t chunk_limit=16, max_inflight=1, scratch_bytes=0;
    // Maximum sector coverage of one vector (including unaligned first/last sector).
    uint64_t vector_sector_bound=512;
    uint32_t dimension=32;
    std::string source="configured_unvalidated";
};
// Host-controlled scoring endpoint, not autonomous graph search. Results are runtime
// handles; each semantic batch waits for every deterministic chunk's real return.
class Controller {
public:
    struct StorageRecord {uint64_t query,task,submitted,completed,sector_bytes,requests,transactions;};
    using Fault=std::function<void(const std::string&,content::Context)>;
    Controller(runtime::Runtime&,content::Handles&,const content::Layout&,LocalReads&,
               transport::PcieTransport&,EventBridge&,EventBridge::Clock,ControllerConfig,Fault={});
    runtime::Handle open(uint64_t query,uint64_t version,const std::vector<float>&);
    runtime::Handle score(uint64_t query,const std::vector<content::Selection>&);
    runtime::Handle close(uint64_t query);
    const std::vector<StorageRecord>& storage() const{return storage_;}
    uint64_t chunk_size() const{return chunk_size_;}
    uint64_t admission_limit() const{return admission_limit_;}
    uint64_t block_bound() const{return block_bound_;}
    const BufferModel& buffer() const{return buffer_;}
    const FifoResource& port() const{return port_;}
    const FifoResource& compute() const{return compute_;}
    std::string diagnostic() const;
    size_t sessions() const{return sessions_.size();}
    size_t pending_protocol() const{return conversation_.pending();}
private:
    struct Session {
        uint64_t query,version,credit=0,token=0,next_chunk=0,outstanding=0,close_task=0;content::Bytes bytes;
        runtime::Handle opened;std::vector<runtime::Handle> batches;bool closing=false;
    };
    struct Chunk {
        uint64_t query,task=0,chunk,credit=0,input,output,scratch;content::Handle data;
        std::vector<content::Selection> selections;content::Bytes result;
        bool data_live=false,error=false;std::string detail;content::Code code=content::Code::BackendFailure;
    };
    runtime::Runtime& runtime_;content::Handles& handles_;const content::Layout& layout_;LocalReads& reads_;
    transport::PcieTransport& transport_;EventBridge& bridge_;EventBridge::Clock now_;ControllerConfig config_;Fault fault_;
    BufferModel buffer_;FifoResource port_,compute_;transport::Conversation conversation_;
    uint64_t query_bytes_,chunk_size_,block_bound_,admission_limit_,admitted_=0,next_token_=0;
    std::map<uint64_t,std::shared_ptr<Session>> sessions_;
    std::deque<std::function<void()>> admissions_;
    std::vector<StorageRecord> storage_;
    void admit();
    void fail_open(const std::shared_ptr<Session>&,uint64_t,const std::exception&);
    void close_ready(const std::shared_ptr<Session>&);
    void send(transport::Message,std::function<void(transport::Message)>);
    transport::Message message(transport::Kind,const Session&,uint64_t task) const;
    void stage(content::Context,const std::string&,uint64_t,std::function<void()>);
    void inject(const std::string&,content::Context);
    runtime::Handle chunk(const std::shared_ptr<Session>&,std::vector<content::Selection>);
    void fail_chunk(const std::shared_ptr<Session>&,const std::shared_ptr<Chunk>&,const std::exception&);
    void release_input(const std::shared_ptr<Chunk>&);
};
}
