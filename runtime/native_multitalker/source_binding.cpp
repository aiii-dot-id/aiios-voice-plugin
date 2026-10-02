#include "source_binding.h"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace aii::multitalker {
namespace {
constexpr size_t maximum_samples=480000;
void check(bool ok,const char* why) {if(!ok)throw SourceContractViolation(why);}
void pcm_valid(const std::vector<float>& pcm,bool normalized) {
  check(!pcm.empty()&&pcm.size()<=maximum_samples,"source PCM extent");
  for(float x:pcm)check(std::isfinite(x)&&(!normalized||std::abs(x)<=1),"source PCM range");
}
void evidence_matches(const aii::voice::RecognizedSegment& row,const std::vector<float>& pcm) {
  check(!row.text.empty()&&row.text.size()<=131071&&row.start<row.end&&row.end<=pcm.size(),
        "source transcript extent");
  if(row.evidence.empty()) {
    check(row.evidence_regions.empty(),"source regions lack PCM");return;
  }
  check(row.evidence_unavailable.empty()&&row.evidence.size()>=31920&&row.evidence.size()<=160000,
        "source evidence disposition");
  auto regions=row.evidence_regions;
  if(regions.empty()) {
    check(row.evidence_start<=pcm.size()&&row.evidence.size()<=pcm.size()-row.evidence_start,
          "source evidence extent");
    regions.emplace_back(row.evidence_start,row.evidence_start+row.evidence.size());
  }
  check(regions.size()<=128&&regions.front().first==row.evidence_start,"source evidence origin");
  uint64_t previous=row.start;size_t copied=0;
  for(const auto& region:regions) {
    check(region.first>=previous&&region.first<region.second&&region.second<=row.end,
          "source evidence region");
    const auto n=region.second-region.first;
    check(n<=row.evidence.size()-copied,"source evidence length");
    check(std::equal(pcm.begin()+size_t(region.first),pcm.begin()+size_t(region.second),
                     row.evidence.begin()+copied),"identity evidence differs from recognized source");
    copied+=size_t(n);previous=region.second;
  }
  check(copied==row.evidence.size(),"source evidence census");
}
}
Waveforms normalize_sources(const std::vector<float>& mixture,Waveforms raw) {
  pcm_valid(mixture,true);
  for(const auto& source:raw) {
    pcm_valid(source,false);check(source.size()==mixture.size(),"separator truncated a source");
  }
  double energy=0;for(float x:mixture)energy+=double(x)*x;
  const double input_rms=std::sqrt(energy/mixture.size());
  for(auto& source:raw) {
    double peak=0;for(float x:source)peak=std::max(peak,std::abs(double(x)));
    if(!peak||!input_rms){std::fill(source.begin(),source.end(),0.0f);continue;}
    double scaled_energy=0;for(float x:source){const double scaled=x/peak;scaled_energy+=scaled*scaled;}
    const double gain=std::min(input_rms/std::sqrt(scaled_energy/source.size()),.95);
    for(auto& x:source)x=static_cast<float>((x/peak)*gain);
  }
  return raw;
}
std::vector<aii::voice::RecognizedSegment> bind_source_text(aii::voice::Recognizer& recognizer,
    const Waveforms& sources,uint64_t capture,const std::atomic<bool>& cancelled,
    const std::function<void()>& completed_stage) {
  check(capture>0&&recognizer.separated(),"source binding requires a speaker-aware recognizer");
  for(const auto& source:sources)pcm_valid(source,true);
  check(sources[0].size()==sources[1].size(),"source clocks differ");
  auto alive=[&]{if(cancelled.load()){recognizer.cancel();throw aii::voice::Cancelled("source binding cancelled");}};
  std::vector<aii::voice::RecognizedSegment> result;
  for(size_t source=0;source<sources.size();++source) {
    const auto& pcm=sources[source];alive();
    // Independent recognition, including a fresh diarizer capture. Its track
    // number is never carried into the other waveform as a presumed person.
    try {
      recognizer.open();
      alive();recognizer.begin();alive();
      for(size_t offset=0;offset<pcm.size();offset+=997) {
        alive();check(recognizer.push(pcm.data()+offset,std::min(size_t(997),pcm.size()-offset)).empty(),
                      "speaker-aware recognizer returned pooled text");
        if(completed_stage)completed_stage();
      }
      alive();check(recognizer.finish_with_progress(completed_stage).empty(),"speaker-aware recognizer returned pooled final");alive();
      auto rows=recognizer.segments();
      check(rows.size()<=64&&result.size()+rows.size()<=128,"source segment bound");
      for(size_t i=0;i<rows.size();++i) {
        auto& row=rows[i];evidence_matches(row,pcm);
        row.track="capture-"+std::to_string(capture)+".source-"+std::to_string(source)+".track-"+std::to_string(i);
        check(row.track.size()<=63,"source track identifier bound");
        result.push_back(std::move(row));
      }
    }catch(...) {recognizer.reset();throw;}
    recognizer.reset();alive();
    if(completed_stage)completed_stage();
  }
  alive();return result;
}
}
