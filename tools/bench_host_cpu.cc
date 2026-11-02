// Local wall-clock diagnostic; never changes simulation parameters.
#include "operators/operators.h"
#include <algorithm>
#include <chrono>
#include <iostream>
#include <random>
#include <functional>
using namespace ndpsim::operators;
volatile uint64_t checksum = 0;
double measure(const std::function<Result()>& fn) {
    using Clock = std::chrono::steady_clock;
    for (int i=0;i<100;++i) checksum += fn().bytes.size();
    std::vector<double> samples;
    for (int trial=0;trial<7;++trial) {
        unsigned iterations=0;
        auto start=Clock::now();
        double elapsed;
        do {
            for (int i=0;i<100;++i) {
                auto r=fn();
                checksum += r.bytes.size() + (r.bytes.empty()?0:r.bytes[0]);
            }
            iterations+=100;
            elapsed=std::chrono::duration<double,std::nano>(Clock::now()-start).count();
        } while(elapsed<20000000);
        samples.push_back(elapsed/iterations);
    }
    std::sort(samples.begin(),samples.end());
    return samples[3];
}
int main() {
    std::mt19937 rng(42);
    std::uniform_real_distribution<float> value(0,255);
    std::vector<float> q(128); for(auto& x:q)x=value(rng);
    auto query=fp32(q);
    std::cout<<"kind,batch,dimension,checks,comparisons,measured_ns,model_ns,measured_over_model\n";
    for(unsigned n: {1,16,32,128}) {
        std::vector<ndpsim::content::Bytes> vectors;
        std::vector<uint32_t> ids;
        for(unsigned i=0;i<n;++i) {
            for(auto& x:q)x=value(rng);
            vectors.push_back(fp32(q)); ids.push_back(i);
        }
        auto distance=[&]{return squared_l2(query,vectors,ids);};
        auto result=distance();
        double predicted=3+(result.work.flops+3)/4;
        double ns=measure(distance);
        std::cout<<"distance,"<<n<<",128,0,0,"<<ns<<","<<predicted<<","<<ns/predicted<<"\n";
        auto control=[&]{return candidates(result.bytes,10);};
        auto work=control().work;
        predicted=1+work.candidate_checks+work.comparisons;
        ns=measure(control);
        std::cout<<"control,"<<n<<",128,"<<work.candidate_checks<<","<<work.comparisons<<","<<ns<<","<<predicted<<","<<ns/predicted<<"\n";
    }
    std::cerr<<"checksum="<<checksum<<"\n";
}
