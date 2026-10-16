#include "refinement.h"
#include <fstream>
#include <iostream>
using namespace aii::multitalker;
namespace {
std::vector<float> load(const char* path,size_t maximum) {
  std::ifstream f(path,std::ios::binary|std::ios::ate);const auto n=f.tellg();
  if(!f || n<=0 || n%4 || uint64_t(n)>maximum*4)throw std::runtime_error("float extent");
  std::vector<float> out(size_t(n)/4);f.seekg(0);
  if(!f.read(reinterpret_cast<char*>(out.data()),n))throw std::runtime_error("float read");
  return out;
}
}
int main(int argc,char** argv){try{
  if(argc<4 || (argc-1)%3)throw std::runtime_error("PCM, original and refined activity triples required");
  for(int i=1;i<argc;i+=3) {
    const auto pcm=load(argv[i],RefinementCapture::maximum_samples);
    const auto original=load(argv[i+1],(RefinementCapture::maximum_samples/160+1)*8);
    const auto refined=load(argv[i+2],(RefinementCapture::maximum_samples/160+1)*8);
    RefinementCapture capture;
    for(size_t at=0;at<pcm.size();at+=16000)
      capture.append(pcm.data()+at,std::min(size_t(16000),pcm.size()-at));
    for(size_t at=0;at<original.size();at+=14*64) {
      const auto end=std::min(original.size(),at+14*64);
      capture.targets(nemotron_targets({original.begin()+at,original.begin()+end},(end-at+63)/64));
    }
    capture.seal(original.size()/8);
    const bool accepted=capture.matches(refined);
    auto evidence=capture.evidence(accepted?refined:original);
    std::cout<<"{\"case\":"<<(i-1)/3<<",\"accepted\":"<<(accepted?"true":"false")<<",\"allowed_samples\":[";
    for(size_t track=0;track<8;++track) {
      if(track)std::cout<<',';
      std::cout<<evidence.audio.attribution_track(track,evidence.activity).pcm.size();
    }
    std::cout<<"]}"<<std::endl;
  }
  return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
