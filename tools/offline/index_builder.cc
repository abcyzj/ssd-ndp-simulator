// Offline only: Faiss never enters SST's functional or timing path.
#include <faiss/IndexNSG.h>
#include <omp.h>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>
#include <cmath>
static void need(bool b, const char* s) { if (!b) throw std::runtime_error(s); }
static void u32(std::ostream& f, uint32_t v) {
    for (int i=0;i<4;++i) f.put(char((v>>(8*i))&255));
}
static std::vector<float> load(const char* path, size_t n, unsigned d) {
    std::ifstream f(path,std::ios::binary);
    std::vector<float> x(n*d);
    for (auto& v:x) {
        unsigned char b[4]; f.read(reinterpret_cast<char*>(b),4);
        need(bool(f),"truncated FP32");
        uint32_t bits=uint32_t(b[0])|(uint32_t(b[1])<<8)|(uint32_t(b[2])<<16)|(uint32_t(b[3])<<24);
        static_assert(sizeof(float)==4); __builtin_memcpy(&v,&bits,4);
        need(std::isfinite(v),"nonfinite input");
    }
    need(f.peek()==EOF,"extra FP32 bytes"); return x;
}
int main(int argc,char** argv) {
    try {
        need(argc==10,"usage: builder base query N d Q threads graph report diagnostic_ids");
        unsigned n=std::stoul(argv[3]),d=std::stoul(argv[4]),q=std::stoul(argv[5]),t=std::stoul(argv[6]);
        need(n>=1000 && n<=10000000 && d>0 && d<=65536 && q>0 && t>=1 && t<=4,"invalid builder bounds");
        omp_set_num_threads(t); omp_set_dynamic(0);
        auto x=load(argv[1],n,d), queries=load(argv[2],q,d);
        faiss::IndexNSGFlat index(d,64);
        index.GK=64; index.build_type=1; index.nsg.L=200; index.nsg.C=500;
        index.add(n,x.data());
        auto g=index.nsg.final_graph;
        need(g && g->N==int(n) && g->K==64 && index.ntotal==n,"graph shape mismatch");
        need(index.nsg.enterpoint>=0 && unsigned(index.nsg.enterpoint)<n,"invalid entry");
        std::ofstream out(argv[7],std::ios::binary);
        for(unsigned i=0;i<n;++i) {
            std::vector<uint32_t> ids; bool sentinel=false;
            for(int j=0;j<64;++j) {
                int v=g->at(i,j);
                if(v<0) { need(v==-1,"unknown sentinel"); sentinel=true; continue; }
                need(!sentinel && unsigned(v)<n,"edge ID or sentinel mismatch"); ids.push_back(v);
            }
            u32(out,ids.size()); for(auto v:ids) u32(out,v);
            for(size_t j=ids.size();j<64;++j) u32(out,0);
        }
        need(bool(out),"graph write failed"); out.close();
        index.nsg.search_L=256;
        std::vector<float> distances(size_t(q)*10); std::vector<faiss::idx_t> ids(size_t(q)*10);
        index.search(q,queries.data(),10,distances.data(),ids.data());
        std::ofstream diag(argv[9],std::ios::binary);
        for(auto id:ids) { need(id>=0 && id<n,"diagnostic ID out of bounds"); u32(diag,id); }
        need(bool(diag),"diagnostic write failed");
        std::ofstream report(argv[8]);
        report<<"{\"entry\":"<<index.nsg.enterpoint<<",\"max_degree\":64,\"parameters\":{\"R\":64,\"GK\":64,\"L\":200,\"C\":500,\"build_type\":1,\"search_L\":256}}\n";
        need(bool(report),"report write failed");
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
