#include "microphone.h"
#include "speaker_evidence.h"
#include <cstring>
#include <fstream>
#include <iostream>

namespace {
std::vector<float> load(const char* path,size_t maximum) {
  std::ifstream f(path,std::ios::binary|std::ios::ate);
  const auto n=f.tellg();
  if(!f || n<=0 || static_cast<uint64_t>(n)>maximum*4 || n%4)
    throw std::runtime_error("invalid float file");
  std::vector<float> values(static_cast<size_t>(n)/4);
  f.seekg(0);if(!f.read(reinterpret_cast<char*>(values.data()),n))throw std::runtime_error("float file read");
  return values;
}
}
int main(int argc,char** argv) {
  try {
    if(argc<4)throw std::invalid_argument("graph root, mel coefficients, mono float PCM required");
    const auto mel=load(argv[2],128*257);
    aii::multitalker::Microphone mic(argv[1],mel.data(),mel.size());
    for(int file=3;file<argc;++file) {
    const auto pcm=load(argv[file],16000*600);mic.reset(static_cast<uint64_t>(file-2));
    std::array<std::vector<int64_t>,4> tokens;
    std::vector<float> activity;
    aii::multitalker::SpeakerEvidence evidence;
    size_t chunks=0,maximum=0;
    auto collect=[&](const std::vector<aii::multitalker::MicrophoneUpdate>& updates){
      for(const auto& update:updates) {
        ++chunks;
        if(update.activity_start_frame!=activity.size()/4)throw std::runtime_error("activity clock gap");
        evidence.push(update.activity_start_frame,update.activity);
        activity.insert(activity.end(),update.activity.begin(),update.activity.end());
        if(update.end_sample>pcm.size() || update.end_sample<=update.start_sample)
          throw std::runtime_error("microphone span outside audio");
        for(const auto& track:update.tracks)for(const auto& t:track.tokens)tokens[track.track].push_back(t.id);
      }
      maximum=std::max(maximum,mic.retained_samples());
    };
    // Deliberately unrelated to model batch size or browser worklet blocks.
    for(size_t offset=0;offset<pcm.size();offset+=997)
      collect(mic.accept(pcm.data()+offset,std::min(size_t(997),pcm.size()-offset)));
    collect(mic.finish());
    if(maximum>16000*3)throw std::runtime_error("unbounded capture buffering");
    std::cout<<"{\"samples\":"<<pcm.size()<<",\"chunks\":"<<chunks
             <<",\"peak_retained_samples\":"<<maximum<<",\"tracks\":[";
    for(size_t track=0;track<4;++track) {
      if(track)std::cout<<',';
      std::cout<<'[';
      for(size_t i=0;i<tokens[track].size();++i){if(i)std::cout<<',';std::cout<<tokens[track][i];}
      std::cout<<']';
    }
    std::cout<<"],\"activity\":[";
    for(size_t i=0;i<activity.size();++i){if(i)std::cout<<',';std::cout<<activity[i];}
    std::cout<<"],\"evidence_spans\":[";
    const auto spans=evidence.finish(pcm.size());
    for(size_t i=0;i<spans.size();++i){if(i)std::cout<<',';const auto& s=spans[i];
      std::cout<<"{\"track\":"<<s.track<<",\"start\":"<<s.start<<",\"end\":"<<s.end<<",\"active_samples\":"<<s.active_samples<<'}';}
    std::cout<<"]}"<<std::endl;
    }
    return 0;
  } catch(const std::exception& e){std::cerr<<e.what()<<std::endl;return 1;}
}
