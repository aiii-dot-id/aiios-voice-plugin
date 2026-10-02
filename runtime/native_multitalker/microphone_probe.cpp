#include "microphone.h"
#include "speaker_evidence.h"
#include "evidence_audio.h"
#include <cstring>
#include <fstream>
#include <iostream>
#include <chrono>

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
    aii::multitalker::NemotronConfig config;
    int arg=3;
    if(std::string(argv[arg])=="--nemotron" || std::string(argv[arg])=="--nemotron-reference") {
      if(argc<7)throw std::invalid_argument("Nemotron model, gpu and audio required");
      config={argv[arg+1],std::stoi(argv[arg+2]),std::string(argv[arg])!="--nemotron-reference"};arg+=3;
    }
    const size_t channels=config.model.empty()?4:8;
    aii::multitalker::EncoderExecution execution;
    while(arg+1<argc) {
      const std::string option=argv[arg];
      if(option=="--encoder-threads")execution.threads=std::stoi(argv[++arg]);
      else if(option=="--refine-evidence")config.refine_evidence=true;
      else if(option=="--encoder-cuda")execution.cuda_device=std::stoi(argv[++arg]);
      else if(option=="--encoder-profile")execution.profile_prefix=argv[++arg];
      else break;
      ++arg;
    }
    if(arg>=argc)throw std::invalid_argument("mono float PCM required");
    const auto load_start=std::chrono::steady_clock::now();
    aii::multitalker::Microphone mic(argv[1],mel.data(),mel.size(),config,execution);
    const double load_seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-load_start).count();
    const bool continuous=std::string(argv[arg])=="--continue";
    const int first=arg+(continuous?1:0);
    if(argc<=first)throw std::invalid_argument("mono float PCM required");
    for(int file=first;file<argc;++file) {
    const auto pcm=load(argv[file],16000*600);mic.reset(static_cast<uint64_t>(file-first+1),continuous&&file>first);
    const auto run_start=std::chrono::steady_clock::now();
    std::array<std::vector<int64_t>,8> tokens;
    std::vector<float> activity;
    aii::multitalker::SpeakerEvidence evidence(channels,channels==8?160:1280);
    aii::multitalker::EvidenceAudio evidence_audio;
    size_t chunks=0,maximum=0,maximum_diar=0;
    auto collect=[&](const std::vector<aii::multitalker::MicrophoneUpdate>& updates){
      for(const auto& update:updates) {
        ++chunks;
        if(update.activity_start_frame!=activity.size()/channels)throw std::runtime_error("activity clock gap");
        if(!update.activity.empty())evidence.push(update.activity_start_frame,update.activity);
        evidence_audio.collect(evidence.regions());
        activity.insert(activity.end(),update.activity.begin(),update.activity.end());
        if(update.end_sample>pcm.size() || update.end_sample<=update.start_sample)
          throw std::runtime_error("microphone span outside audio");
        for(const auto& track:update.tracks)for(const auto& t:track.tokens)tokens[track.track].push_back(t.id);
      }
      maximum=std::max(maximum,mic.retained_samples());
      maximum_diar=std::max(maximum_diar,mic.retained_diarization_frames());
    };
    // Deliberately unrelated to model batch size or browser worklet blocks.
    for(size_t offset=0;offset<pcm.size();offset+=997) {
      const auto count=std::min(size_t(997),pcm.size()-offset);
      evidence_audio.append(pcm.data()+offset,count);
      collect(mic.accept(pcm.data()+offset,count));
    }
    collect(mic.finish());
    auto spans=evidence.finish(pcm.size());
    evidence_audio.collect(evidence.regions());
    const auto retained_refinement=mic.refinement_retained_samples();
    if(auto refined=mic.refine_evidence()) {
      evidence=std::move(refined->activity);evidence_audio=std::move(refined->audio);
      spans=evidence.spans();
    }
    if(maximum>16000*6)throw std::runtime_error("unbounded capture buffering");
    if(channels==8 && maximum_diar>512)throw std::runtime_error("unbounded Nemotron output buffering");
    std::cout<<"{\"samples\":"<<pcm.size()<<",\"chunks\":"<<chunks<<",\"load_seconds\":"<<load_seconds
             <<",\"inference_seconds\":"<<std::chrono::duration<double>(std::chrono::steady_clock::now()-run_start).count()
             <<",\"peak_retained_samples\":"<<maximum<<",\"peak_retained_diarization_frames\":"<<maximum_diar
             <<",\"refinement_status\":\""<<mic.refinement_status()<<"\",\"refinement_seconds\":"<<mic.refinement_seconds()
             <<",\"refinement_retained_samples\":"<<retained_refinement<<",\"tracks\":[";
    for(size_t track=0;track<channels;++track) {
      if(track)std::cout<<',';
      std::cout<<'[';
      for(size_t i=0;i<tokens[track].size();++i){if(i)std::cout<<',';std::cout<<tokens[track][i];}
      std::cout<<']';
    }
    std::cout<<"],\"activity\":[";
    for(size_t i=0;i<activity.size();++i){if(i)std::cout<<',';std::cout<<activity[i];}
    std::cout<<"],\"evidence_spans\":[";
    for(size_t i=0;i<spans.size();++i){if(i)std::cout<<',';const auto& s=spans[i];
      std::cout<<"{\"track\":"<<s.track<<",\"start\":"<<s.start<<",\"end\":"<<s.end<<",\"active_samples\":"<<s.active_samples<<'}';}
    std::cout<<"],\"selected_evidence\":[";
    for(size_t track=0;track<channels;++track) {
      if(track)std::cout<<',';
      const auto& selected=evidence_audio.track(track);
      std::cout<<"{\"track\":"<<track<<",\"samples\":"<<selected.pcm.size()<<",\"regions\":[";
      for(size_t i=0;i<selected.regions.size();++i) {
        if(i)std::cout<<',';
        std::cout<<'['<<selected.regions[i].first<<','<<selected.regions[i].second<<']';
      }
      std::cout<<"]}";
    }
    // Raw clean islands above are diagnostic only. Whole-final attribution
    // must use the same coverage decision as the resident recognizer.
    std::cout<<"],\"attribution_policy\":\"whole-final-coverage-v1\",\"attribution_evidence\":[";
    for(size_t track=0;track<channels;++track) {
      if(track)std::cout<<',';
      const auto& selected=evidence_audio.attribution_track(track,evidence);
      const auto& observed=evidence.activity(track);
      std::cout<<"{\"track\":"<<track<<",\"samples\":"<<selected.pcm.size()
               <<",\"reason\":\""<<evidence_audio.unavailable_reason(track,evidence)
               <<"\",\"overlap_samples\":"<<observed.overlap
               <<",\"competing_uncertain_samples\":"<<observed.competing_uncertain<<",\"regions\":[";
      for(size_t i=0;i<selected.regions.size();++i) {
        if(i)std::cout<<',';
        std::cout<<'['<<selected.regions[i].first<<','<<selected.regions[i].second<<']';
      }
      std::cout<<"]}";
    }
    std::cout<<"]}"<<std::endl;
    }
    return 0;
  } catch(const std::exception& e){std::cerr<<e.what()<<std::endl;return 1;}
}
