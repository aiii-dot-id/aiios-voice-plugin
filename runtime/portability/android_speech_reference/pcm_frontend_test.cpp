#include "native_pcm_frontend.h"
#include <iostream>
#include <limits>
using aii::voice::pixel::PcmFrontend;
void demand(bool value,const char* why){if(!value)throw std::runtime_error(why);}
template<class F> bool refused(F f){try{f();return false;}catch(const std::exception&){return true;}}
int main(){try{
    PcmFrontend frontend;
    for(size_t n:{258,320,16000,79999,80000}){
        std::vector<float> pcm(n);
        for(size_t i=0;i<n;++i)pcm[i]=static_cast<float>(.4*std::sin(i*.027));
        auto result=frontend.process(pcm);
        demand(result.size()==64000,"feature shape differs");
        for(auto x:result)demand(std::isfinite(x),"nonfinite features");
        for(size_t m=0;m<128;++m)for(size_t t=n/160;t<500;++t)
            demand(result[m*500+t]==0,"invented feature padding");
        demand(frontend.process(pcm)==result,"frontend retained preceding state");
    }
    for(size_t n:{0,257,80001})demand(refused([&]{frontend.process(std::vector<float>(n));}),"invalid PCM size accepted");
    for(float x:{std::numeric_limits<float>::quiet_NaN(),std::numeric_limits<float>::infinity()}){
        std::vector<float> pcm(512);pcm[257]=x;
        demand(refused([&]{frontend.process(pcm);}),"nonfinite PCM accepted");
    }
    int visited=0;std::vector<float> pcm(80000,.5f);
    demand(refused([&]{frontend.process(pcm,[&]{if(++visited==11)throw std::runtime_error("cancelled");});}),"frontend ignored cancellation");
    demand(visited==11,"frontend continued after cancellation");
    demand(frontend.process(pcm).size()==64000,"recovery after frontend cancellation failed");
    std::cout<<"PCM bounds, padding, cancellation and recovery passed\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
