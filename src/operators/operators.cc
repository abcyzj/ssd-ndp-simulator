#include "operators/operators.h"
#include <cmath>
#include <cstring>
#include <cstdio>
#include <limits>
#include <set>
namespace ndpsim::operators {
using content::Code;
static_assert(sizeof(float)==4 && sizeof(double)==8 && std::numeric_limits<float>::is_iec559 && std::numeric_limits<double>::is_iec559);
namespace {
void put(content::Bytes& b,uint64_t x,unsigned n) {for(unsigned i=0;i<n;++i)b.push_back(uint8_t(x>>(8*i)));}
uint64_t get(const content::Bytes& b,size_t at,unsigned n) {uint64_t x=0;for(unsigned i=0;i<n;++i)x|=uint64_t(b.at(at+i))<<(8*i);return x;}
std::vector<float> decode(const content::Bytes& b) {
    if(b.empty() || b.size()%4)throw content::Error(Code::InvalidArgument,"FP32 nonempty dimensions required");
    std::vector<float> out;for(size_t i=0;i<b.size();i+=4){auto u=uint32_t(get(b,i,4));float f;std::memcpy(&f,&u,4);if(!std::isfinite(f))throw content::Error(Code::InvalidArgument,"nonfinite FP32");out.push_back(f);}return out;
}
void pack(content::Bytes& b,Score s) {put(b,s.id,4);uint64_t u;std::memcpy(&u,&s.distance,8);put(b,u,8);}
bool less(Score a,Score b) {return a.distance<b.distance || (a.distance==b.distance && a.id<b.id);}
}
content::Bytes fp32(const std::vector<float>& v) {content::Bytes b;for(float f:v){if(!std::isfinite(f))throw content::Error(Code::InvalidArgument,"nonfinite FP32");uint32_t u;std::memcpy(&u,&f,4);put(b,u,4);}return b;}
std::vector<Score> scores(const content::Bytes& b) {
    if(b.size()%12)throw content::Error(Code::InvalidArgument,"invalid packed scores");
    std::vector<Score> out;for(size_t i=0;i<b.size();i+=12){auto u=get(b,i+4,8);double d;std::memcpy(&d,&u,8);if(!std::isfinite(d)||d<0)throw content::Error(Code::InvalidArgument,"invalid squared distance");out.push_back({uint32_t(get(b,i,4)),d});}return out;
}
Result squared_l2(const content::Bytes& query,const std::vector<content::Bytes>& vectors,const std::vector<uint32_t>& ids) {
#ifdef NDPSIM_SLOW_FUNCTIONAL
    static const bool announced=[]{std::fputs("NDPSIM_FUNCTIONAL_VARIANT repeated_l2=2048\n",stderr);return true;}();
    (void)announced;
#endif
    auto q=decode(query);if(vectors.size()!=ids.size())throw content::Error(Code::InvalidArgument,"vector/ID count mismatch");
    if(vectors.size()>UINT64_MAX/12 || q.size()>UINT64_MAX/4 || (vectors.size() && q.size()>UINT64_MAX/4/vectors.size()))throw content::Error(Code::Overflow,"L2 work overflow");
    Result r;std::set<uint32_t> unique;
    for(size_t j=0;j<vectors.size();++j){if(!unique.insert(ids[j]).second)throw content::Error(Code::InvalidArgument,"duplicate vector ID");auto v=decode(vectors[j]);if(v.size()!=q.size())throw content::Error(Code::InvalidArgument,"L2 dimension mismatch");double sum=0;for(size_t i=0;i<q.size();++i){double delta=double(q[i])-double(v[i]);double square=delta*delta;sum=sum+square;}
#ifdef NDPSIM_SLOW_FUNCTIONAL
        // Same dimension order and result; volatile prevents dead-code elimination.
        // This is simulator implementation overhead, never target hardware work.
        for(unsigned repeat=0;repeat<2048;++repeat){volatile double repeated=0;for(size_t i=0;i<q.size();++i){double delta=double(q[i])-double(v[i]);double square=delta*delta;repeated=repeated+square;}if(repeated!=sum)throw std::logic_error("equivalent L2 mismatch");}
#endif
        pack(r.bytes,{ids[j],sum});}
    r.work.elements=q.size()*vectors.size();r.work.flops=3*r.work.elements;
    if(4*r.work.elements>UINT64_MAX-query.size())throw content::Error(Code::Overflow,"L2 byte count overflow");
    r.work.read_bytes=query.size()+4*r.work.elements;r.work.write_bytes=r.work.output_bytes=r.bytes.size();return r;
}
Result candidates(const content::Bytes& packed,uint64_t limit) {
    auto input=scores(packed);std::vector<Score> selected;Result r;r.work.read_bytes=packed.size();
    for(auto s:input){++r.work.candidate_checks;bool found=false;for(auto& old:selected){++r.work.comparisons;if(old.id==s.id){++r.work.comparisons;if(less(s,old))old=s;found=true;break;}}if(!found)selected.push_back(s);}
    for(size_t i=1;i<selected.size();++i){auto s=selected[i];size_t j=i;while(j){++r.work.comparisons;if(!less(s,selected[j-1]))break;selected[j]=selected[j-1];--j;}selected[j]=s;}
    if(limit<selected.size())selected.resize(size_t(limit));for(auto s:selected)pack(r.bytes,s);
    r.work.elements=input.size();r.work.write_bytes=r.work.output_bytes=r.bytes.size();return r;
}
}
