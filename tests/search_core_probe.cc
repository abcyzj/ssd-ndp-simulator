// Synchronous fixture harness only, not a native benchmark or simulation runner.
#include "search/core.h"
#include <algorithm>
#include <iostream>
#include <iomanip>
using namespace ndpsim;
void emit(const char* stage, const operators::Result& r) {
    std::cout << stage << ' ';
    if (r.bytes.empty()) std::cout << '-';
    for (auto b : r.bytes) std::cout << std::hex << std::setw(2) << std::setfill('0') << unsigned(b);
    const auto& w = r.work;
    std::cout << std::dec << ' ' << w.elements << ' ' << w.flops << ' ' << w.read_bytes
              << ' ' << w.write_bytes << ' ' << w.output_bytes << ' ' << w.candidate_checks
              << ' ' << w.comparisons << '\n';
}
std::vector<uint32_t> unpack(const content::Bytes& b) {
    std::vector<uint32_t> out;
    for (size_t i=0; i<b.size(); i+=4) {
        uint32_t id=0;
        for (unsigned j=0; j<4; ++j) id |= uint32_t(b.at(i+j)) << (8*j);
        out.push_back(id);
    }
    return out;
}
int main() {
    try {
        size_t n,d,k,L,B,E,chunk;
        if (!(std::cin >> n >> d >> k >> L >> B >> E >> chunk)) return 2;
        std::vector<float> q(d);
        for (auto& x:q) std::cin >> x;
        std::vector<content::Bytes> vectors;
        for (size_t i=0;i<n;++i) {
            std::vector<float> v(d);
            for (auto& x:v) std::cin >> x;
            vectors.push_back(operators::fp32(v));
        }
        std::vector<std::vector<uint32_t>> graph(n);
        for (auto& row:graph) {
            size_t degree; std::cin >> degree;
            row.resize(degree); for (auto& id:row) std::cin >> id;
        }
        std::set<uint32_t> scored,expanded;
        content::Bytes all;
        std::vector<uint32_t> fresh{0};
        for (;;) {
            if (!fresh.empty()) {
                content::Bytes batch;
                for (size_t i=0;i<fresh.size();i+=chunk) {
                    auto end=std::min(fresh.size(),i+chunk);
                    std::vector<uint32_t> ids(fresh.begin()+i,fresh.begin()+end);
                    std::vector<content::Bytes> v;
                    for (auto id:ids) {scored.insert(id);v.push_back(vectors.at(id));}
                    auto r=operators::squared_l2(operators::fp32(q),v,ids);
                    emit("score",r);batch.insert(batch.end(),r.bytes.begin(),r.bytes.end());
                }
                auto r=search::core::merge_candidates(all,batch,n);
                emit("merge",r);all=std::move(r.bytes);
            }
            auto r=search::core::select_frontier(all,expanded,L,B,E);
            emit("select",r);auto chosen=unpack(r.bytes);
            if (chosen.empty()) {
                auto bytes=all;bytes.resize(std::min(k,bytes.size()/12)*12);
                emit("result",{bytes,{}});break;
            }
            std::vector<std::vector<uint32_t>> rows;
            for (auto id:chosen) {expanded.insert(id);rows.push_back(graph.at(id));}
            auto dedup=search::core::dedup_neighbors(rows,scored,n);
            emit("dedup",dedup);fresh=unpack(dedup.bytes);
        }
        // Hand-counted duplicate-ID merge, plus empty and invalid boundaries.
        auto scores=operators::squared_l2(operators::fp32({0}),
            {operators::fp32({1}),operators::fp32({-1}),operators::fp32({2})},{9,2,7});
        content::Bytes duplicate(scores.bytes.begin(),scores.bytes.begin()+12);
        emit("duplicate",search::core::merge_candidates(scores.bytes,duplicate,2));
        emit("empty-select",search::core::select_frontier({}, {}, 8, 4, 2));
        emit("empty-dedup",search::core::dedup_neighbors({{}}, {}, n));
        emit("empty-merge",search::core::merge_candidates({}, {}, n));
        try {search::core::dedup_neighbors({{uint32_t(n)}},{},n);return 3;}
        catch(const content::Error& e) {if(e.code!=content::Code::InvalidArgument)return 4;}
        try {search::core::select_frontier({1},{},1,1,1);return 5;}
        catch(const content::Error& e) {if(e.code!=content::Code::InvalidArgument)return 6;}
    } catch(const std::exception& e) {std::cerr << e.what() << '\n';return 1;}
}
