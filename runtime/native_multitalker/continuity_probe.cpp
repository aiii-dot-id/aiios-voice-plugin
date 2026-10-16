#include "microphone.h"
#include <fstream>
#include <iostream>
#include <stdexcept>

namespace {
void check(bool value,const char* why) {if(!value)throw std::runtime_error(why);}
std::vector<float> load(const char* path,size_t maximum) {
  std::ifstream f(path,std::ios::binary|std::ios::ate);const auto n=f.tellg();
  check(f && n>0 && n%4==0 && uint64_t(n)<=maximum*4,"float input extent");
  std::vector<float> result(size_t(n)/4);f.seekg(0);
  check(bool(f.read(reinterpret_cast<char*>(result.data()),n)),"float input read");return result;
}
struct Result {
  std::array<std::vector<int64_t>,8> tokens;
  std::vector<float> activity;
};
Result run(aii::multitalker::Microphone& mic,const std::vector<float>& pcm,size_t channels) {
  Result result;
  auto collect=[&](const auto& updates) {
    for(const auto& u:updates) {
      check(u.activity_start_frame==result.activity.size()/channels,"utterance activity clock not reset");
      check(u.end_sample<=pcm.size(),"utterance PCM clock not reset");
      result.activity.insert(result.activity.end(),u.activity.begin(),u.activity.end());
      for(const auto& track:u.tracks)for(const auto& token:track.tokens)
        result.tokens.at(track.track).push_back(token.id);
    }
  };
  for(size_t offset=0;offset<pcm.size();offset+=997)
    collect(mic.accept(pcm.data()+offset,std::min(size_t(997),pcm.size()-offset)));
  collect(mic.finish());return result;
}
}
int main(int argc,char** argv) {try {
  check(argc==4||argc==6||argc==7,"graph root, mel coefficients, mono float PCM, optional Nemotron model, gpu and encoder CUDA device required");
  const auto mel=load(argv[2],128*257),pcm=load(argv[3],16000*60);
  aii::multitalker::NemotronConfig config;
  if(argc>=6)config={argv[4],std::stoi(argv[5])};
  aii::multitalker::EncoderExecution execution;
  if(argc==7)execution.cuda_device=std::stoi(argv[6]);
  const size_t channels=argc>=6?8:4;
  aii::multitalker::Microphone mic(argv[1],mel.data(),mel.size(),config,execution);
  mic.reset(1);auto baseline=run(mic,pcm,channels);
  const auto first=mic.retained_diarization_frames();check(channels==8||first>0,"no diarizer evidence");
  mic.reset(2,true);check(mic.retained_diarization_frames()==first,"utterance discarded speaker cache");
  const auto continued=run(mic,pcm,channels);(void)continued;
  const auto second=mic.retained_diarization_frames();
  check(channels==8?second==0:(second>0 && second<=aii::multitalker::DiarCache::cache_limit+aii::multitalker::DiarCache::fifo_limit),"speaker output/cache unbounded");
  // A new session cannot see the preceding session's identity evidence or ASR.
  mic.reset(3);check(mic.retained_diarization_frames()==0,"session leaked speaker cache");
  const auto fresh=run(mic,pcm,channels);
  check(fresh.tokens==baseline.tokens && fresh.activity==baseline.activity,"fresh session differs from isolated baseline");
  mic.reset(4,true);mic.cancel();bool refused=false;
  try {mic.reset(5,true);}catch(const std::exception&){refused=true;}
  check(refused,"cancelled capture was continued");
  mic.reset(6);check(mic.retained_diarization_frames()==0,"cancel recovery kept speaker cache");
  const auto recovered=run(mic,pcm,channels);
  check(recovered.tokens==baseline.tokens && recovered.activity==baseline.activity,"cancel recovery differs from baseline");
  std::cout<<"{\"passed\":true,\"first_frames\":"<<first<<",\"continued_frames\":"<<second
           <<",\"session_isolation\":true,\"cancellation_recovery\":true,\"speaker_accuracy_qualified\":false}\n";
  return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
