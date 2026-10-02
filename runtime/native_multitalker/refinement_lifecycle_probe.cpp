#include "microphone.h"
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <thread>
using namespace aii::multitalker;
namespace {
void check(bool b,const char* message){if(!b)throw std::runtime_error(message);}
std::vector<float> load(const char* path,size_t maximum) {
  std::ifstream f(path,std::ios::binary|std::ios::ate);const auto n=f.tellg();
  check(f&&n>0&&n%4==0&&uint64_t(n)<=maximum*4,"float extent");
  std::vector<float> out(size_t(n)/4);f.seekg(0);
  check(bool(f.read(reinterpret_cast<char*>(out.data()),n)),"float read");return out;
}
struct Trace {
  std::vector<float> activity;
  std::array<std::vector<int64_t>,8> tokens;
  bool operator==(const Trace& other) const{return activity==other.activity&&tokens==other.tokens;}
};
Trace run(Microphone& mic,const std::vector<float>& pcm) {
  Trace trace;
  auto collect=[&](const auto& updates){for(const auto& u:updates){
    check(u.activity_start_frame==trace.activity.size()/8,"activity clock");
    trace.activity.insert(trace.activity.end(),u.activity.begin(),u.activity.end());
    for(const auto& track:u.tracks)for(const auto& token:track.tokens)trace.tokens.at(track.track).push_back(token.id);
  }};
  for(size_t at=0;at<pcm.size();at+=997)collect(mic.accept(pcm.data()+at,std::min(size_t(997),pcm.size()-at)));
  collect(mic.finish());return trace;
}
size_t dominant(const std::vector<float>& a,size_t frame) {
  size_t best=0;for(size_t t=1;t<8;++t)if(a[frame*8+t]>a[frame*8+best])best=t;
  return best;
}
// The next utterance after a replay against the same continuation after none,
// both on fresh sessions. Accepted and refused replays must both leave it
// exactly unchanged: activity values and every track's tokens.
std::string continuation(Microphone& mic,uint64_t& epoch,const std::vector<float>& pcm,bool& unchanged,
                         std::string& status) {
  mic.reset(++epoch);run(mic,pcm);mic.reset(++epoch,true);const auto unrefined=run(mic,pcm);
  mic.reset(++epoch);run(mic,pcm);const bool accepted=bool(mic.refine_evidence());
  status=mic.refinement_status();
  check(accepted==(status=="identical_masks")&&(accepted||status=="changed_masks"),"replay did not run");
  mic.reset(++epoch,true);const auto next=run(mic,pcm);
  unchanged=next==unrefined;
  float largest=0;size_t dominant_changed=0,tracks_changed=0;
  const size_t frames=std::min(next.activity.size(),unrefined.activity.size())/8;
  for(size_t i=0;i<frames*8;++i)largest=std::max(largest,std::abs(next.activity[i]-unrefined.activity[i]));
  for(size_t f=0;f<frames;++f)dominant_changed+=dominant(next.activity,f)!=dominant(unrefined.activity,f);
  for(size_t t=0;t<8;++t)tracks_changed+=next.tokens[t]!=unrefined.tokens[t];
  std::ostringstream out;
  out<<"{\"replay\":\""<<status<<"\",\"next_utterance_unchanged_by_replay\":"<<(unchanged?"true":"false")
     <<",\"next_frames\":"<<next.activity.size()/8<<",\"unrefined_frames\":"<<unrefined.activity.size()/8
     <<",\"activity_max_abs_difference\":"<<largest<<",\"dominant_track_changed_frames\":"<<dominant_changed
     <<",\"transcript_tracks_changed\":"<<tracks_changed<<"}";
  return out.str();
}
}
int main(int argc,char** argv){try{
  check(argc>=6,"graphs, mel, Nemotron model, GPU, PCM required");
  const auto mel=load(argv[2],128*257),pcm=load(argv[5],RefinementCapture::maximum_samples);
  Microphone mic(argv[1],mel.data(),mel.size(),{argv[3],std::stoi(argv[4]),true,true});
  mic.reset(1);
  bool refused=false;try{mic.refine_evidence();}catch(const std::runtime_error&){refused=true;}
  check(refused,"early refinement admitted");
  const auto baseline=run(mic,pcm);size_t completed_calls=0;
  auto first=mic.refine_evidence(true,[&]{++completed_calls;});
  const std::string first_status=mic.refinement_status();
  check(completed_calls==(pcm.size()+15999)/16000+1,
        "refinement lost a completed model call or invented a heartbeat");
  check(bool(first)==(first_status=="identical_masks"),"replay status disagrees with its result");
  check(mic.refinement_retained_samples()==0&&mic.retained_diarization_frames()==0,"refinement retained private buffers");
  refused=false;try{mic.refine_evidence();}catch(const std::runtime_error&){refused=true;}
  check(refused,"second refinement admitted");
  // An utterance may continue after refinement. Cancel the next replay from
  // another thread; the inference owner alone retires its stream copy.
  mic.reset(2,true);run(mic,pcm);
  const auto start=std::chrono::steady_clock::now();
  std::thread cancel([&]{std::this_thread::sleep_for(std::chrono::milliseconds(50));mic.cancel();});
  bool cancelled=false;
  try{mic.refine_evidence();}catch(const std::runtime_error& e){cancelled=std::string(e.what()).find("cancel")!=std::string::npos;}
  cancel.join();const double seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
  check(cancelled&&seconds<5,"replay did not cancel within probe bound");
  check(mic.refinement_retained_samples()==0,"cancel kept refinement PCM");
  refused=false;try{mic.reset(3,true);}catch(const std::runtime_error&){refused=true;}
  check(refused,"cancelled refinement continued");
  mic.reset(4);const auto recovered=run(mic,pcm);auto after=mic.refine_evidence();
  check(recovered==baseline&&bool(after)==bool(first)&&mic.refinement_status()==first_status,
        "fresh session differs after cancellation");
  if(first)for(size_t t=0;t<8;++t)check(first->audio.attribution_track(t,first->activity).pcm==
      after->audio.attribution_track(t,after->activity).pcm,"recovered identity evidence differs");
  mic.reset(5);mic.accept(pcm.data(),std::min(size_t(997),pcm.size()));
  check(mic.refinement_retained_samples()>0,"missing retained cancellation fixture");
  mic.cancel();refused=false;
  try{mic.refine_evidence();}catch(const std::runtime_error&){refused=true;}
  check(refused&&mic.refinement_retained_samples()==0,"early cancellation kept PCM");
  mic.reset(6);mic.accept(pcm.data(),std::min(size_t(997),pcm.size()));
  float invalid=std::numeric_limits<float>::quiet_NaN();refused=false;
  try{mic.accept(&invalid,1);}catch(const std::invalid_argument&){refused=true;}
  check(refused&&mic.refinement_retained_samples()==0,"invalid input kept PCM");
  mic.reset(7);mic.finish();check(!mic.refine_evidence(false),"unneeded replay ran");
  // Every recording: the continuation after its replay, accepted or refused,
  // against the same continuation after none.
  uint64_t epoch=7;bool all_unchanged=true;size_t accepted=0,changed=0;
  std::ostringstream continuations;
  for(int i=5;i<argc;++i) {
    const auto recording=i==5?pcm:load(argv[i],RefinementCapture::maximum_samples);
    bool unchanged=false;std::string status;
    if(i>5)continuations<<',';
    continuations<<continuation(mic,epoch,recording,unchanged,status);
    all_unchanged=all_unchanged&&unchanged;
    (status=="identical_masks"?accepted:changed)+=1;
  }
  std::cout<<"{\"passed\":"<<(all_unchanged?"true":"false")<<",\"cancel_seconds\":"<<seconds
      <<",\"first_replay\":\""<<first_status<<"\",\"exact_recovery\":true,\"continued_utterance\":true"
      <<",\"next_utterance_unchanged_by_replay\":"<<(all_unchanged?"true":"false")
      <<",\"accepted_replays\":"<<accepted<<",\"refused_replays\":"<<changed
      <<",\"continuations\":["<<continuations.str()<<"]"
      <<",\"duplicate_refused\":true,\"fault_cleanup\":true,\"speaker_accuracy_qualified\":false}\n";
  return all_unchanged?0:1;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
