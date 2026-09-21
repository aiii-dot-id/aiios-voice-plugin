#include "speaker_evidence.h"
#include "evidence_audio.h"
#include <cmath>
#include <iostream>
#include <stdexcept>
using namespace aii::multitalker;
void check(bool b){if(!b)throw std::runtime_error("speaker evidence contract");}
template<class F> void refuses(F f){bool failed=false;try{f();}catch(const std::invalid_argument&){failed=true;}check(failed);}
std::vector<float> activity(size_t frames,int track,int other=-1){
  std::vector<float> out(frames*4,.001f);
  for(size_t f=0;f<frames;++f){if(track>=0)out[f*4+track]=.99f;if(other>=0)out[f*4+other]=.99f;}
  return out;
}
int main(){try{
  EvidenceAudio audio;std::vector<float> first(16000,.25f),second(16000,.75f);
  for(int i=0;i<4;++i)audio.append(first.data(),first.size());
  audio.select({{0,2560,60000,40000}});
  for(int i=0;i<100;++i)audio.append(second.data(),second.size());
  check(audio.track(0).pcm.size()==57440&&audio.track(0).pcm.front()==.25f&&audio.track(0).pcm.back()==.25f);
  check(audio.retained_samples()<=EvidenceAudio::capacity+4*SpeakerEvidence::maximum_span);
  audio.select({{1,2560,60000,40000}});check(audio.track(1).pcm.empty());
  refuses([&]{audio.select({{4,0,40000,40000}});});
  auto invalid_pcm=second;invalid_pcm.back()=NAN;
  const auto before=audio.retained_samples();refuses([&]{audio.append(invalid_pcm.data(),invalid_pcm.size());});check(before==audio.retained_samples());
  SpeakerEvidence solo;solo.push(0,activity(50,2));auto spans=solo.finish(64000);
  check(spans.size()==1&&spans[0].track==2&&spans[0].start==2560&&spans[0].end==61440);
  refuses([&]{solo.push(50,activity(1,2));});
  SpeakerEvidence overlap;overlap.push(0,activity(100,0,1));check(overlap.finish(128000).empty());
  SpeakerEvidence short_voice;short_voice.push(0,activity(24,0));check(short_voice.finish(30720).empty());
  SpeakerEvidence silence;silence.push(0,activity(100,-1));check(silence.finish(128000).empty());
  SpeakerEvidence unknown;auto p=activity(50,0);for(size_t i=0;i<50;++i)p[i*4+1]=.2f;
  unknown.push(0,p);check(unknown.finish(64000).empty());
  SpeakerEvidence alternating;alternating.push(0,activity(40,0));alternating.push(40,activity(40,1));
  spans=alternating.finish(102400);check(spans.size()==2&&spans[0].end<spans[1].start);
  SpeakerEvidence paused;paused.push(0,activity(20,0));paused.push(20,activity(8,-1));paused.push(28,activity(20,0));
  check(paused.finish(61440).size()==1);
  SpeakerEvidence delayed_other;delayed_other.push(0,activity(40,0));delayed_other.push(40,activity(20,-1));delayed_other.push(60,activity(40,1));
  spans=delayed_other.finish(128000);check(spans.size()==2&&spans[0].end==40*1280-2560);
  SpeakerEvidence invalid;p=activity(50,0);p.back()=NAN;refuses([&]{invalid.push(0,p);});
  invalid.push(0,activity(50,0));refuses([&]{invalid.push(49,activity(1,0));});
  check(invalid.finish(64000).size()==1);
  SpeakerEvidence bounded;for(size_t i=0;i<10000;++i){bounded.push(i*100,activity(100,0));check(bounded.retained_spans()<=4);}
  spans=bounded.finish(1280000000);check(spans.size()==1&&spans[0].end-spans[0].start<=SpeakerEvidence::maximum_span);
  SpeakerEvidence partial;partial.push(0,activity(50,0));spans=partial.finish(60000);check(spans.size()==1&&spans[0].end<=60000);
  std::cout<<"speaker-specific evidence selection contracts passed\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
