// Offline M0 feasibility and locked API probe; not the T2 index importer.
#include <faiss/IndexNSG.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <queue>
#include <set>
#include <stdexcept>
#include <vector>
#include <omp.h>

static void require(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(message);
}
static std::vector<float> load(const char* path, bool bin, unsigned n, unsigned d) {
    std::ifstream f(path, std::ios::binary);
    require(bool(f), "input open failed");
    uint32_t header[2];
    if (bin) {
        f.read(reinterpret_cast<char*>(header), 8);
        require(bool(f) && header[0] >= n && header[1] == d, "fbin header mismatch");
    }
    std::vector<float> x(size_t(n) * d);
    for (unsigned i=0; i<n; ++i) {
        if (!bin) {
            f.read(reinterpret_cast<char*>(header), 4);
            require(bool(f) && header[0] == d, "fvecs header mismatch");
        }
        f.read(reinterpret_cast<char*>(x.data()+size_t(i)*d), d*4);
        require(bool(f), "truncated input");
    }
    for (float v:x) require(std::isfinite(v), "non-finite input");
    return x;
}
int main(int argc, char** argv) {
    try {
        require(argc==9, "usage: probe base query fvecs|fbin N d threads adjacency report");
        const bool bin=std::string(argv[3])=="fbin";
        require(bin || std::string(argv[3])=="fvecs", "unsupported format");
        unsigned n=std::stoul(argv[4]), d=std::stoul(argv[5]), threads=std::stoul(argv[6]);
        require(n>=1000 && n<=1000000 && (d==96 || d==128) && threads>=1 && threads<=4, "invalid probe bounds");
        omp_set_num_threads(threads);
        omp_set_dynamic(0);
        auto x=load(argv[1],bin,n,d), q=load(argv[2],bin,32,d);
        faiss::IndexNSGFlat index(d,64);
        index.GK=64;
        index.build_type=1; // Mandatory: NNDescent; never implicit brute force.
        index.nsg.L=200;
        index.nsg.C=500;
        index.add(n,x.data());
        auto g=index.nsg.final_graph;
        require(g && g->N==int(n) && g->K==64 && index.ntotal==n, "graph shape mismatch");
        require(index.nsg.enterpoint>=0 && unsigned(index.nsg.enterpoint)<n, "invalid entry");
        std::ofstream out(argv[7],std::ios::binary);
        require(bool(out), "graph open failed");
        unsigned mind=64,maxd=0;
        uint64_t edges=0,duplicates=0,self_edges=0;
        for (unsigned i=0;i<n;++i) {
            std::vector<uint32_t> row(65,0);
            std::set<int> seen;
            bool sentinel=false;
            for (int j=0;j<64;++j) {
                int v=g->at(i,j);
                if (v<0) { require(v==-1,"unknown sentinel"); sentinel=true; continue; }
                require(!sentinel && unsigned(v)<n,"invalid edge ID or noncontiguous sentinel");
                if(!seen.insert(v).second) ++duplicates;
                if(unsigned(v)==i) ++self_edges;
                row[++row[0]]=v;
            }
            mind=std::min(mind,row[0]); maxd=std::max(maxd,row[0]); edges+=row[0];
            out.write(reinterpret_cast<char*>(row.data()),row.size()*4);
        }
        require(bool(out),"graph write failed");
        out.close();
        std::vector<bool> visited(n,false);
        std::queue<unsigned> todo;
        todo.push(index.nsg.enterpoint); visited[index.nsg.enterpoint]=true;
        unsigned reached=0;
        while (!todo.empty()) {
            auto u=todo.front();todo.pop();++reached;
            for (int j=0;j<64;++j) {
                int v=g->at(u,j); if(v<0) break;
                if(!visited[v]) {visited[v]=true;todo.push(v);}
            }
        }
        require(reached==n,"entry does not reach entire graph");
        index.nsg.search_L=256;
        std::vector<float> distances(32*10);
        std::vector<faiss::idx_t> ids(32*10);
        index.search(32,q.data(),10,distances.data(),ids.data());
        // Independent fixed-dimension-order FP64 exact oracle on this base subset.
        unsigned hits=0;
        for(unsigned qi=0;qi<32;++qi) {
            std::vector<std::pair<double,unsigned>> top;
            for(unsigned i=0;i<n;++i) {
                double distance=0;
                for(unsigned j=0;j<d;++j) {
                    double delta=double(x[size_t(i)*d+j])-double(q[size_t(qi)*d+j]);
                    distance+=delta*delta;
                }
                std::pair<double,unsigned> value{distance,i};
                if(top.size()<10 || value<top.back()) {
                    top.insert(std::lower_bound(top.begin(),top.end(),value),value);
                    if(top.size()>10) top.pop_back();
                }
            }
            for(auto v:top)
                if(std::find(ids.begin()+qi*10,ids.begin()+(qi+1)*10,v.second)!=ids.begin()+(qi+1)*10) ++hits;
        }
        std::ofstream report(argv[8]);
        report<<"{\"n\":"<<n<<",\"d\":"<<d<<",\"threads\":"<<threads
              <<",\"entry\":"<<index.nsg.enterpoint<<",\"reachable\":"<<reached
              <<",\"degree_min\":"<<mind<<",\"degree_max\":"<<maxd<<",\"edges\":"<<edges
              <<",\"duplicate_edges\":"<<duplicates<<",\"self_edges\":"<<self_edges
              <<",\"recall_at_10\":"<<double(hits)/320<<",\"query_rows\":32"
              <<",\"parameters\":{\"R\":64,\"GK\":64,\"L\":200,\"C\":500,\"build_type\":1,\"search_L\":256}"
              <<",\"rng\":{\"nsg_default\":2307,\"nsg_init_graph_local\":4660,\"nndescent_internal_default\":2021}"
              <<",\"scope\":\"prefix base and first 32 query rows, M0 feasibility only\"}\n";
        require(bool(report),"report write failed");
        require(double(hits)/320>=0.95,"builder diagnostic Recall@10 below 0.95");
        std::cout<<"M0 Faiss graph export/coverage/reachability/quality PASS\n";
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
