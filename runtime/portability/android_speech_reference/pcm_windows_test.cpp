#include "native_pcm_windows.h"
#include <cstring>
#include <iostream>
#include <limits>
using aii::voice::pixel::PcmWindows;
void demand(bool value,const char* why){if(!value)throw std::runtime_error(why);}
template<class F> bool refused(F f){try{f();return false;}catch(const std::exception&){return true;}}
int main(){try{
    for(size_t n:{0,1,258,15999,16000,16001,79999,80000,80001,218880,960000}){
      std::vector<float> pcm(n);for(size_t i=0;i<n;++i)pcm[i]=float(int(i%501)-250)/512;
      for(const std::vector<size_t>& packets:{std::vector<size_t>{1},std::vector<size_t>{512},std::vector<size_t>{17,1024,511,32000}}){
        PcmWindows owner;std::vector<uint64_t> ends;size_t index=0,offset=0;
        auto consume=[&](const PcmWindows::Window& w){
          demand(w.start==(w.end>80000?w.end-80000:0) && w.end<=n,"window source range differs");
          demand(w.pcm.size()==w.end-w.start && std::memcmp(w.pcm.data(),pcm.data()+w.start,w.pcm.size()*4)==0,"window PCM bits differ");
          ends.push_back(w.end);
        };
        while(offset<n){const size_t take=std::min(packets[index++%packets.size()],n-offset);owner.accept(offset,pcm.data()+offset,take,consume);offset+=take;}
        owner.finish(n,consume);owner.finish(n,consume);
        std::vector<uint64_t> expected;for(uint64_t end=16000;end<=n;end+=16000)expected.push_back(end);
        if(n%16000)expected.push_back(n);
        demand(ends==expected,"packet timing changed windows or final tail duplicated");
        demand(owner.received()==n && owner.emitted()==n && owner.retained()==std::min(size_t(80000),n),"source clock or memory bound differs");
        float more=0;demand(refused([&]{owner.accept(n,&more,1,consume);}),"input revived after Finish");
      }
    }
    PcmWindows owner;std::vector<float> pcm(32000,.4f);auto ignore=[](const auto&){};
    demand(refused([&]{owner.accept(1,pcm.data(),1,ignore);}),"source gap accepted");
    demand(refused([&]{owner.accept(0,pcm.data(),32001,ignore);}),"oversized packet accepted");
    pcm.back()=std::numeric_limits<float>::quiet_NaN();
    demand(refused([&]{owner.accept(0,pcm.data(),pcm.size(),ignore);}) && owner.received()==0,"invalid packet partly consumed");
    pcm.back()=0;owner.accept(0,pcm.data(),16001,ignore);
    demand(refused([&]{owner.finish(16000,ignore);}),"wrong Finish cutoff accepted");
    demand(refused([&]{owner.accept(16001,pcm.data(),16000,[](const auto&){throw std::runtime_error("consumer fault");});}),"consumer error disappeared");
    demand(refused([&]{owner.finish(owner.received(),ignore);}),"failed stream resumed as successful");
    std::cout<<"packet-invariant windows, source bits, bounded storage and exact tail passed\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
