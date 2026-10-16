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
void sparse_prefix_recovery() {
  EvidenceAudio audio;SpeakerEvidence selector(8,160);
  std::vector<float> pcm(160,.2f);
  for(size_t f=0;f<1600;++f) {
    audio.append(pcm.data(),pcm.size());
    std::vector<float> p(8,.001f);
    if(f<20||(f>=320&&f<340)||(f>=640&&f<660)||f>=980)p[0]=.99f;
    selector.push(f,p);audio.collect(selector.regions());
  }
  selector.finish(256000);audio.collect(selector.regions());
  const auto& recovered=audio.track(0);
  check(recovered.span.active_samples>=32000&&!recovered.pcm.empty());
  check(recovered.regions.size()==1&&recovered.regions[0].first>=160000);
  check(audio.retained_samples()<=EvidenceAudio::capacity+8*SpeakerEvidence::maximum_span);
  check(audio.track(1).pcm.empty());
  // Replacement must improve real evidence, never an uncertain/overlap span.
  // A full clean selection remains intact when a later region is weaker.
  EvidenceAudio good;std::vector<float> second(16000,.8f);
  for(int i=0;i<10;++i)good.append(second.data(),second.size());
  good.collect({{0,0,160000,160000}});
  good.append(second.data(),second.size());good.collect({{0,160000,176000,1000}});
  check(good.track(0).span.active_samples==160000&&good.track(0).regions.size()==1);
}
void fragmented_speech_can_request_refinement_without_granting_identity() {
  for(const auto native:{false,true}) {
    const size_t channels=native?8:4;
    const uint64_t cadence=native?160:1280;
    SpeakerEvidence selector(channels,cadence);EvidenceAudio audio;
    std::vector<float> pcm(cadence,.2f);
    const uint64_t duration=46080;
    for(uint64_t start=0;start<duration;start+=cadence) {
      audio.append(pcm.data(),pcm.size());
      std::vector<float> p(channels,.001f);
      p[0]=(start<23040||(start>=24320&&start<32000)||start>=33280)?.99f:.5f;
      selector.push(start/cadence,p);audio.collect(selector.regions());
    }
    selector.finish(duration);audio.collect(selector.regions());
    check(audio.track(0).pcm.empty());
    check(audio.attribution_track(0,selector).pcm.empty());
    check(audio.refinement_needed(0,selector));
    check(!audio.refinement_needed(1,selector));
    check(std::string(audio.unavailable_reason(0,selector))=="speaker_activity_uncertain");
  }
  EvidenceAudio empty;
  SpeakerEvidence short_voice;auto p=activity(24,0);p[0]=.5f;
  short_voice.push(0,p);short_voice.finish(30720);
  check(!empty.refinement_needed(0,short_voice));
  SpeakerEvidence overlap;overlap.push(0,activity(50,0,1));overlap.finish(64000);
  check(!empty.refinement_needed(0,overlap));
}
void clean_island_does_not_name_overlapping_words() {
  for(const auto native:{false,true})for(const auto overlap_first:{false,true}) {
    const size_t channels=native?8:4;
    const uint64_t cadence=native?160:1280;
    SpeakerEvidence selector(channels,cadence);EvidenceAudio audio;
    std::vector<float> pcm(cadence,.2f);
    const size_t solo_frames=64000/cadence,overlap_frames=25600/cadence;
    for(size_t frame=0;frame<solo_frames+overlap_frames;++frame) {
      audio.append(pcm.data(),pcm.size());
      std::vector<float> p(channels,.001f);p[0]=.99f;
      const bool overlapping=overlap_first?frame<overlap_frames:frame>=solo_frames;
      if(overlapping)p[channels-1]=.99f;
      selector.push(frame,p);audio.collect(selector.regions());
    }
    selector.finish((solo_frames+overlap_frames)*cadence);audio.collect(selector.regions());
    // The old implementation returned usable PCM and an empty reason here,
    // allowing the prefix (or suffix) to label the whole mixed transcript.
    check(!audio.track(0).pcm.empty());
    check(audio.attribution_track(0,selector).pcm.empty());
    check(std::string(audio.unavailable_reason(0,selector))=="speaker_track_coverage_unverified");
    check(audio.attribution_track(channels-1,selector).pcm.empty());
    // A new single-speaker utterance can still be identified; no sticky
    // conversation-wide quarantine, model-slot carryover or PCM borrowing.
    selector=SpeakerEvidence(channels,cadence);audio=EvidenceAudio{};
    for(size_t frame=0;frame<solo_frames;++frame) {
      audio.append(pcm.data(),pcm.size());
      std::vector<float> p(channels,.001f);p[0]=.99f;
      selector.push(frame,p);audio.collect(selector.regions());
    }
    selector.finish(solo_frames*cadence);audio.collect(selector.regions());
    check(!audio.attribution_track(0,selector).pcm.empty());
    check(std::string(audio.unavailable_reason(0,selector)).empty());
  }
}
void uncertain_competitor_does_not_inherit_clean_identity() {
  for(const auto native:{false,true})for(const auto competing_first:{false,true}) {
    const size_t channels=native?8:4;
    const uint64_t cadence=native?160:1280;
    for(const float target:{.5f,.99f})for(const float other:{.1001f,.5f,.8999f}) {
      SpeakerEvidence selector(channels,cadence);EvidenceAudio audio;
      std::vector<float> pcm(cadence,.2f);
      const size_t solo=64000/cadence,competing=25600/cadence;
      for(size_t frame=0;frame<solo+competing;++frame) {
        audio.append(pcm.data(),pcm.size());
        std::vector<float> p(channels,.001f);p[0]=.99f;
        if(competing_first?frame<competing:frame>=solo){p[0]=target;p[channels-1]=other;}
        selector.push(frame,p);audio.collect(selector.regions());
      }
      selector.finish((solo+competing)*cadence);audio.collect(selector.regions());
      check(selector.activity(0).overlap==0);
      check(selector.activity(0).competing_uncertain==25600);
      check(!audio.track(0).pcm.empty());
      check(audio.attribution_track(0,selector).pcm.empty());
      check(std::string(audio.unavailable_reason(0,selector))=="speaker_track_coverage_unverified");
      // Own-track uncertainty is different: no competitor was observed.
      // It still trims evidence, but cannot create a competing speaker flag.
      selector=SpeakerEvidence(channels,cadence);audio=EvidenceAudio{};
      for(size_t frame=0;frame<solo+competing;++frame) {
        audio.append(pcm.data(),pcm.size());
        std::vector<float> p(channels,.001f);p[0]=frame<solo?.99f:.5f;
        p[channels-1]=.1f;
        selector.push(frame,p);audio.collect(selector.regions());
      }
      selector.finish((solo+competing)*cadence);audio.collect(selector.regions());
      check(selector.activity(0).competing_uncertain==0);
      check(!audio.attribution_track(0,selector).pcm.empty());
      check(std::string(audio.unavailable_reason(0,selector)).empty());
    }
  }
}
int main(){try{
  fragmented_speech_can_request_refinement_without_granting_identity();
  sparse_prefix_recovery();
  clean_island_does_not_name_overlapping_words();
  uncertain_competitor_does_not_inherit_clean_identity();
  EvidenceAudio audio;std::vector<float> first(16000,.25f),second(16000,.75f);
  for(int i=0;i<4;++i)audio.append(first.data(),first.size());
  audio.collect({{0,2560,60000,40000}});
  for(int i=0;i<100;++i)audio.append(second.data(),second.size());
  check(audio.track(0).pcm.size()==57440&&audio.track(0).pcm.front()==.25f&&audio.track(0).pcm.back()==.25f);
  check(audio.retained_samples()<=EvidenceAudio::capacity+4*SpeakerEvidence::maximum_span);
  audio.collect({{1,2560,60000,40000}});check(audio.track(1).pcm.empty());
  refuses([&]{audio.collect({{8,0,40000,40000}});});
  refuses([&]{audio.collect({{0,2560,60000,40000}});});
  auto invalid_pcm=second;invalid_pcm.back()=NAN;
  const auto before=audio.retained_samples();refuses([&]{audio.append(invalid_pcm.data(),invalid_pcm.size());});check(before==audio.retained_samples());
  SpeakerEvidence solo;solo.push(0,activity(50,2));auto spans=solo.finish(64000);
  check(spans.size()==1&&spans[0].track==2&&spans[0].start==2560&&spans[0].end==61440);
  refuses([&]{solo.push(50,activity(1,2));});
  SpeakerEvidence overlap;overlap.push(0,activity(100,0,1));check(overlap.finish(128000).empty());
  EvidenceAudio missing;
  check(overlap.activity(0).overlap==128000&&overlap.activity(1).overlap==128000);
  check(std::string(missing.unavailable_reason(0,overlap))=="speaker_overlap_without_isolated_evidence");
  SpeakerEvidence short_voice;short_voice.push(0,activity(24,0));check(short_voice.finish(30720).empty());
  check(std::string(missing.unavailable_reason(0,short_voice))=="speaker_evidence_too_short");
  SpeakerEvidence silence;silence.push(0,activity(100,-1));check(silence.finish(128000).empty());
  check(std::string(missing.unavailable_reason(0,silence))=="speaker_activity_unavailable");
  SpeakerEvidence unknown;auto p=activity(50,0);for(size_t i=0;i<50;++i)p[i*4+1]=.2f;
  unknown.push(0,p);check(unknown.finish(64000).empty());
  check(unknown.activity(0).overlap==0&&unknown.activity(0).uncertain==64000);
  check(std::string(missing.unavailable_reason(0,unknown))=="speaker_activity_uncertain");
  check(std::string(audio.unavailable_reason(1,unknown))=="speaker_evidence_expired");
  check(std::string(audio.unavailable_reason(0,unknown))=="speaker_track_coverage_unverified");
  // The native diarizer has eight slots at a different cadence. Diagnostics
  // must follow its sample clock too, and disappear at the next utterance.
  SpeakerEvidence native(8,160);std::vector<float> native_p(300*8,.001f);
  for(size_t i=0;i<300;++i){native_p[i*8+5]=.99f;native_p[i*8+7]=.99f;}
  native.push(0,native_p);check(native.finish(48000).empty());
  check(native.activity(5).active==48000&&native.activity(7).overlap==48000);
  check(std::string(missing.unavailable_reason(7,native))=="speaker_overlap_without_isolated_evidence");
  native=SpeakerEvidence(8,160);audio=EvidenceAudio{};
  check(native.activity(7).active==0&&native.activity(7).overlap==0);
  check(std::string(audio.unavailable_reason(7,native))=="speaker_activity_unavailable");
  // An uncertain own-track frame is not silence. It must not join two
  // individually insufficient pieces into a supposedly clean voiceprint.
  SpeakerEvidence uncertain_own;uncertain_own.push(0,activity(20,0));
  p=activity(8,-1);for(size_t i=0;i<8;++i)p[i*4]=.5f;
  uncertain_own.push(20,p);uncertain_own.push(28,activity(20,0));
  check(uncertain_own.finish(61440).empty());
  // Clean islands from the SAME track can supply sufficient evidence without
  // putting any uncertain or competing-track PCM into the embedding input.
  EvidenceAudio islands;SpeakerEvidence selector;
  std::vector<float> clean(16000,.25f),dirty(10240,.9f);
  islands.append(clean.data(),16000);islands.append(clean.data(),9600);
  selector.push(0,activity(20,0));islands.collect(selector.regions());
  islands.append(dirty.data(),dirty.size());p=activity(8,-1);
  for(size_t i=0;i<8;++i)p[4*i]=.5f;
  selector.push(20,p);islands.collect(selector.regions());
  check(islands.track(0).pcm.empty()); // first island is not enough
  islands.append(clean.data(),16000);islands.append(clean.data(),9600);
  selector.push(28,activity(20,0));islands.collect(selector.regions());
  selector.finish(61440);islands.collect(selector.regions());
  const auto& selected=islands.track(0);
  check(selected.pcm.size()==40960&&selected.regions.size()==2);
  check(selected.regions[0]==std::make_pair(uint64_t(2560),uint64_t(23040))&&
    selected.regions[1]==std::make_pair(uint64_t(38400),uint64_t(58880)));
  check(std::all_of(selected.pcm.begin(),selected.pcm.end(),[](float v){return v==.25f;}));
  check(islands.track(1).pcm.empty());
  // A long conversation cannot grow retained evidence or repeat old regions.
  EvidenceAudio bounded_audio;
  for(uint64_t i=0;i<200;++i) {
    bounded_audio.append(clean.data(),clean.size());
    bounded_audio.collect({{0,i*16000,i*16000+16000,16000}});
    check(bounded_audio.retained_samples()<=EvidenceAudio::capacity+4*SpeakerEvidence::maximum_span);
  }
  check(bounded_audio.track(0).pcm.size()==SpeakerEvidence::maximum_span);
  check(bounded_audio.track(0).regions.size()==10);
  // Preserve a usable region before uncertainty, but exclude the uncertain
  // frame and both guard edges from the selected PCM.
  SpeakerEvidence clean_before;clean_before.push(0,activity(40,0));
  p=activity(1,-1);p[0]=.5f;clean_before.push(40,p);
  clean_before.push(41,activity(10,0));spans=clean_before.finish(65280);
  check(spans.size()==1&&spans[0].start==2560&&spans[0].end==48640);
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
  // The final feature fragment has no prediction. Its microphone samples are
  // valid, but cannot be used as positive speaker evidence.
  for(uint64_t actual:{53761,53860,55039}) {
    SpeakerEvidence tail;tail.push(0,activity(42,0));
    spans=tail.finish(actual);
    check(spans.size()==1&&spans[0].end<=42*SpeakerEvidence::hop);
  }
  SpeakerEvidence excessive;excessive.push(0,activity(42,0));
  refuses([&]{excessive.finish(42*SpeakerEvidence::hop+SpeakerEvidence::hop);});
  std::cout<<"speaker-specific evidence selection contracts passed\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
