#include "refinement.h"
#include <iostream>
#include <limits>
using namespace aii::multitalker;
namespace {
void check(bool value){if(!value)throw std::runtime_error("refinement contract");}
template<class F>void refuses(F f){bool failed=false;try{f();}catch(const std::exception&){failed=true;}check(failed);}
RefinementCapture capture(const std::vector<float>& fine,size_t samples) {
  RefinementCapture c;std::vector<float> pcm(16000,.25f);
  for(size_t at=0;at<samples;at+=pcm.size())c.append(pcm.data(),std::min(pcm.size(),samples-at));
  for(size_t at=0;at<fine.size();at+=14*64) {
    auto end=std::min(fine.size(),at+14*64);
    c.targets(nemotron_targets({fine.begin()+at,fine.begin()+end},(end-at+63)/64));
  }
  c.seal(fine.size()/8);return c;
}
}
int main(){try{
  std::vector<float> fine(501*8,0);
  for(size_t f=0;f<501;++f)fine[f*8]=.99f;
  auto c=capture(fine,80003);check(c.available()&&c.matches(fine));
  auto proof=c.evidence(fine);
  check(proof.audio.attribution_track(0,proof.activity).pcm.size()>=32000);
  // Uncertain competition may leave ASR masks identical, but must still veto UID.
  auto competitor=fine;competitor[250*8+7]=.11f;
  check(c.matches(competitor));proof=c.evidence(competitor);
  check(proof.audio.attribution_track(0,proof.activity).pcm.empty());
  auto changed=fine;for(size_t f=0;f<8;++f)changed[f*8]=.49f;
  check(!c.matches(changed));refuses([&]{c.evidence(changed);});
  changed=fine;for(size_t f=8;f<16;++f)changed[f*8+7]=.51f;
  check(!c.matches(changed));
  changed=fine;for(size_t f=0;f<501;++f)std::swap(changed[f*8],changed[f*8+7]);
  check(!c.matches(changed));
  changed=fine;changed.pop_back();check(!c.matches(changed));
  changed=fine;changed.push_back(0);check(!c.matches(changed));
  changed=fine;changed.back()=std::numeric_limits<float>::quiet_NaN();check(!c.matches(changed));
  changed=fine;changed.back()=1.01f;check(!c.matches(changed));
  refuses([&]{c.append(fine.data(),1);});refuses([&]{c.seal(501);});
  // Exact threshold and partial tail, including zero-padded ASR final frames.
  RefinementCapture tail;float sample=.1f;tail.append(&sample,1);
  tail.targets({.5f,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,0});tail.seal(1);
  check(tail.matches({.5f,0,0,0,0,0,0,0}));
  check(!tail.matches({std::nextafter(.5f,1.f),0,0,0,0,0,0,0}));
  // Over-limit capture retires the entire refinement, never just a prefix.
  RefinementCapture long_capture;std::vector<float> second(16000,0);
  for(size_t i=0;i<32;++i)long_capture.append(second.data(),second.size());
  check(long_capture.pcm().size()==RefinementCapture::maximum_samples);
  long_capture.append(&sample,1);check(long_capture.pcm().empty());
  long_capture.targets(std::vector<float>(8,0));long_capture.seal(3201);
  check(!long_capture.available()&&!long_capture.matches({}));
  c.clear();check(!c.available()&&c.pcm().empty());
  std::cout<<"Bounded exact-mask refinement contracts passed\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
