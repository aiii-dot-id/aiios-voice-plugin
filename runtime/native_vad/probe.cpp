#include "vad.h"
#include <chrono>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <vector>
using Clock=std::chrono::steady_clock;
std::vector<char> read(const char* path,size_t limit) {
  std::ifstream f(path,std::ios::binary|std::ios::ate);
  if (!f || f.tellg()<0 || static_cast<size_t>(f.tellg())>limit) throw std::runtime_error("input extent");
  std::vector<char> data(static_cast<size_t>(f.tellg())); f.seekg(0);
  if (!f.read(data.data(),static_cast<std::streamsize>(data.size()))) throw std::runtime_error("input read");
  return data;
}
int main(int argc,char** argv) {
  try {
    if (argc!=3) throw std::runtime_error("model.onnx pcm.f32 required");
    const auto model=read(argv[1],2243022), raw=read(argv[2],16000*120*sizeof(float));
    if (raw.empty() || raw.size()%(512*sizeof(float))) throw std::runtime_error("whole VAD blocks required");
    std::vector<float> pcm(raw.size()/sizeof(float)); std::memcpy(pcm.data(),raw.data(),raw.size());
    char error[1024]{};
    const auto start=Clock::now();
    std::unique_ptr<AiiVad,decltype(&aii_vad_destroy)> v(aii_vad_create(model.data(),model.size(),error,sizeof(error)),aii_vad_destroy);
    if (!v) throw std::runtime_error(error);
    std::cout<<std::setprecision(10)<<"{\"create_seconds\":"<<std::chrono::duration<double>(Clock::now()-start).count()<<",\"probabilities\":[";
    const auto run=Clock::now();
    std::vector<float> first;
    for (size_t i=0;i<pcm.size();i+=512) {
      float p=-7;
      if (aii_vad_feed(v.get(),pcm.data()+i,512,&p,error,sizeof(error))) throw std::runtime_error(error);
      if (i) std::cout<<',';
      std::cout<<p;first.push_back(p);
    }
    const auto seconds=std::chrono::duration<double>(Clock::now()-run).count();
    if (aii_vad_samples(v.get())!=pcm.size()) throw std::runtime_error("sample accounting");
    if (aii_vad_reset(v.get(),error,sizeof(error)) || aii_vad_samples(v.get())) throw std::runtime_error("reset refused");
    // Invalid calls must leave the output, input state and sample clock intact.
    float bad[512]{}, p=-7; bad[4]=std::numeric_limits<float>::quiet_NaN();
    if (aii_vad_feed(v.get(),bad,512,&p,error,sizeof(error))!=-1 || p!=-7 || aii_vad_samples(v.get())) throw std::runtime_error("NaN admission");
    if (aii_vad_feed(v.get(),pcm.data(),511,&p,error,sizeof(error))!=-1 || p!=-7 || aii_vad_samples(v.get())) throw std::runtime_error("short admission");
    bad[4]=std::numeric_limits<float>::infinity();
    if (aii_vad_feed(v.get(),bad,512,&p,error,sizeof(error))!=-1 || p!=-7 || aii_vad_samples(v.get())) throw std::runtime_error("infinity admission");
    for (size_t i=0;i<pcm.size();i+=512) {
      if (aii_vad_feed(v.get(),pcm.data()+i,512,&p,error,sizeof(error))) throw std::runtime_error(error);
      if (p!=first[i/512]) throw std::runtime_error("reset/state parity");
    }
    std::cout<<"],\"seconds\":"<<seconds<<",\"samples\":"<<aii_vad_samples(v.get())<<",\"reset_exact\":true,\"invalid_refused\":true}\n";
    return 0;
  } catch (const std::exception& e) { std::cerr<<e.what()<<'\n';return 1; }
}
