#include "search/graph.h"
#include <algorithm>
namespace ndpsim::search {
using namespace content;
void validate(const Config& c){if(!c.count || c.entry>=c.count || !c.k || c.k>c.count || c.width<c.k || !c.batch || !c.budget || !c.chunk)throw std::invalid_argument("invalid graph search count/entry/k/L/B/E/chunk");}
Bytes ids_bytes(const std::vector<uint32_t>& v){Bytes b;for(auto id:v)for(unsigned j=0;j<4;++j)b.push_back(uint8_t(id>>(8*j)));return b;}
std::vector<uint32_t> ids(const Bytes& b){if(b.size()%4)throw std::invalid_argument("malformed IDs");std::vector<uint32_t> v;for(size_t i=0;i<b.size();i+=4){uint32_t x=0;for(unsigned j=0;j<4;++j)x|=uint32_t(b[i+j])<<(8*j);v.push_back(x);}return v;}
GraphSearch::GraphSearch(runtime::Runtime& r,model::HostCpu& c,EventBridge::Clock n,uint64_t q,Config cfg,Bytes qb,Read read,Trace trace,Done done,Score score):rt_(r),cpu_(c),now_(std::move(n)),query_(q),config_(cfg),query_bytes_(std::move(qb)),read_(std::move(read)),score_(std::move(score)),trace_(std::move(trace)),done_(std::move(done)){validate(cfg);}
void GraphSearch::await(std::vector<runtime::Handle> hs,std::function<void(std::vector<Bytes>)> f){rt_.when_all(hs,query_,[this,hs,f=std::move(f)](runtime::Outcome o){if(o.failure){for(auto h:hs)rt_.release(h);throw std::runtime_error(o.failure->detail);}std::vector<Bytes> values;for(auto h:hs){values.push_back(rt_.try_get(h));rt_.release(h);}f(std::move(values));});}
void GraphSearch::start(){score({config_.entry});}
void GraphSearch::score(std::vector<uint32_t> nodes){
    if(nodes.empty()){select();return;}
    std::vector<runtime::Handle> scores;
    if(score_){for(auto id:nodes)scored_.insert(id);scores.push_back(score_(nodes));}
    else for(size_t i=0;i<nodes.size();i+=config_.chunk){
        auto end=std::min(nodes.size(),i+config_.chunk);std::vector<uint32_t> chunk(nodes.begin()+i,nodes.begin()+end);
        std::vector<runtime::Handle> reads;for(auto id:chunk){scored_.insert(id);reads.push_back(read_(Kind::Vector,id));}
        auto h=cpu_.submit(query_,reads,model::Kind::Distance,[query=query_bytes_,chunk](const auto& v){return operators::squared_l2(query,v,chunk);});
        for(auto read:reads)rt_.release(read);scores.push_back(h);
    }
    auto merged=cpu_.submit(query_,scores,model::Kind::Control,[old=all_,limit=config_.count](const auto& v)mutable{for(const auto& b:v)old.insert(old.end(),b.begin(),b.end());return operators::candidates(old,limit);});
    for(auto h:scores)rt_.release(h);
    await({merged},[this](auto v){all_=std::move(v[0]);select();});
}
void GraphSearch::select(){
    auto h=cpu_.submit(query_,{},model::Kind::Control,[all=all_,expanded=expanded_,cfg=config_](const auto&){
        auto ranked=operators::scores(all);if(ranked.size()>cfg.width)ranked.resize(cfg.width);
        std::vector<uint32_t> chosen;operators::Work work;
        for(auto s:ranked){++work.candidate_checks;if(expanded.size()+chosen.size()<cfg.budget && chosen.size()<cfg.batch && !expanded.count(s.id))chosen.push_back(s.id);}
        auto b=ids_bytes(chosen);work.write_bytes=work.output_bytes=b.size();return operators::Result{std::move(b),work};
    });
    await({h},[this](auto v){auto chosen=ids(v[0]);auto ranked=operators::scores(all_);if(ranked.size()>config_.width)ranked.resize(config_.width);
        trace_({now_(),ranked,chosen});
        if(chosen.empty()){auto result=operators::scores(all_);if(result.size()>config_.k)result.resize(config_.k);done_(result,expanded_.size());return;}
        for(auto id:chosen)expanded_.insert(id);neighbors(std::move(chosen));});
}
void GraphSearch::neighbors(std::vector<uint32_t> chosen){
    std::vector<runtime::Handle> reads;for(auto id:chosen)reads.push_back(read_(Kind::Adjacency,id));
    // Deduplication is a host service with explicit check/comparison counts.
    auto h=cpu_.submit(query_,reads,model::Kind::Control,[this,seen=scored_](const auto& values){
        std::vector<uint32_t> fresh;operators::Work w;
        for(const auto& b:values){auto a=ids(b);if(a.empty() || a[0]+uint64_t(1)>a.size())throw std::invalid_argument("bad adjacency degree");
            for(size_t i=1;i<=a[0];++i){auto id=a[i];if(id>=config_.count)throw std::invalid_argument("bad neighbor ID");++w.candidate_checks;
                // Explicit linear membership and sorted insertion for auditable control work.
                bool found=false;for(auto s:seen){++w.comparisons;if(s==id){found=true;break;}}if(found)continue;
                auto it=fresh.begin();for(;it!=fresh.end();++it){++w.comparisons;if(*it>=id)break;}
                if(it!=fresh.end())++w.comparisons;
                if(it==fresh.end() || *it!=id)fresh.insert(it,id);
            }}auto b=ids_bytes(fresh);w.write_bytes=w.output_bytes=b.size();return operators::Result{std::move(b),w};});
    for(auto r:reads)rt_.release(r);await({h},[this](auto v){score(ids(v[0]));});
}
}
