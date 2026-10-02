#include "source_binding.h"
#include <algorithm>
#include <cmath>
#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace {
using aii::voice::RecognizedSegment;
using aii::multitalker::Waveforms;
void check(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
struct Fake:aii::voice::Recognizer {
  std::vector<float> pcm;
  std::vector<std::vector<float>> inputs;
  size_t opens=0,resets=0,cancels=0;
  std::function<void(Fake&)> on_open,on_push;
  std::function<void(RecognizedSegment&)> alter;
  bool pooled=false,empty=false,joined=false;
  void open() override {++opens;pcm.clear();if(on_open)on_open(*this);}
  void begin() override {}
  std::string push(const float* p,size_t n) override {
    pcm.insert(pcm.end(),p,p+n);if(on_push)on_push(*this);return pooled?"pooled":"";
  }
  std::string finish() override {inputs.push_back(pcm);return {};}
  bool separated() const override {return true;}
  std::vector<RecognizedSegment> segments() const override {
    if(empty)return {};
    RecognizedSegment row;row.track="same-diarizer-slot";
    row.text=pcm.front()>0?"positive source":"negative source";
    row.end=pcm.size();row.evidence=pcm;
    if(joined) {
      row.evidence.assign(pcm.begin(),pcm.begin()+16000);
      row.evidence.insert(row.evidence.end(),pcm.begin()+32000,pcm.begin()+48000);
      row.evidence_regions={{0,16000},{32000,48000}};
    }
    if(alter)alter(row);
    return {row};
  }
  void reset() override {++resets;pcm.clear();}
  void cancel() noexcept override {++cancels;}
};
template<class F>void refuses(F f,const char* why) {
  bool refused=false;try{f();}catch(const std::exception&){refused=true;}check(refused,why);
}
Waveforms sources(size_t n=32000) {return {{std::vector<float>(n,.125f),std::vector<float>(n,-.25f)}};}
}
int main(){try{
  using aii::multitalker::bind_source_text;using aii::multitalker::normalize_sources;
  std::atomic<bool> cancelled{false};const auto pcm=sources();
  Fake normal;auto rows=bind_source_text(normal,pcm,7,cancelled);
  check(rows.size()==2&&normal.opens==2&&normal.resets==2,"independent source lifecycle");
  for(size_t i=0;i<2;++i) {
    check(normal.inputs[i]==pcm[i]&&rows[i].evidence==pcm[i],"text/evidence PCM identity");
    check(rows[i].track=="capture-7.source-"+std::to_string(i)+".track-0","acoustic origin identifier");
  }
  Fake reversed;auto swapped=pcm;std::swap(swapped[0],swapped[1]);
  const auto reversal=bind_source_text(reversed,swapped,8,cancelled);
  check(reversal[0].text==rows[1].text&&reversal[1].text==rows[0].text,"slot permutation changed content binding");
  Fake joined;joined.joined=true;const auto gapped=bind_source_text(joined,sources(48000),9,cancelled);
  check(gapped[0].evidence_regions.size()==2&&gapped[0].evidence.size()==32000,"original-clock region binding");
  for(int fault=0;fault<10;++fault) {
    Fake bad;bad.alter=[fault](RecognizedSegment& row){switch(fault){
      case 0:row.evidence[3]*=-1;break;
      case 1:row.evidence_regions={{0,32001}};break;
      case 2:row.evidence_regions={{1,32000}};break;
      case 3:row.evidence_start=1;break;
      case 4:row.evidence_regions={{0,16000},{15999,31999}};break;
      case 5:row.evidence_unavailable="speaker_track_coverage_unverified";break;
      case 6:row.evidence.resize(31919);break;
      case 7:row.text.clear();break;
      case 8:row.end=32001;break;
      case 9:row.evidence.clear();row.evidence_regions={{0,32000}};break;
    }};
    refuses([&]{bind_source_text(bad,pcm,1,cancelled);},"invalid source binding escaped");
    check(bad.resets==1,"failed source did not retire");
  }
  Fake absent;absent.alter=[](RecognizedSegment& row){row.evidence.clear();row.evidence_unavailable="speaker_track_coverage_unverified";};
  const auto withheld=bind_source_text(absent,pcm,1,cancelled);
  check(withheld.size()==2&&withheld[0].evidence.empty()&&withheld[1].evidence_unavailable=="speaker_track_coverage_unverified",
        "separation invented identity evidence");
  Fake silent;silent.empty=true;check(bind_source_text(silent,pcm,1,cancelled).empty(),"silence invented text");
  Fake pooled;pooled.pooled=true;refuses([&]{bind_source_text(pooled,pcm,1,cancelled);},"pooled text admitted");
  for(int boundary=0;boundary<4;++boundary) {
    Fake stop;
    if(boundary==0)cancelled.store(true);
    if(boundary==1)stop.on_open=[&](Fake&){cancelled.store(true);};
    if(boundary==2)stop.on_push=[&](Fake&){cancelled.store(true);};
    if(boundary==3)stop.on_open=[&](Fake& f){if(f.opens==2)cancelled.store(true);};
    bool caught=false;try{bind_source_text(stop,pcm,1,cancelled);}catch(const aii::voice::Cancelled&){caught=true;}
    check(caught&&stop.cancels==1,"cancellation did not discard unfinished binding");
    cancelled.store(false);stop.on_open={};stop.on_push={};
    check(bind_source_text(stop,pcm,2,cancelled).size()==2,"binding recovery failed");
  }
  Fake bad_second;auto invalid=pcm;invalid[1][4]=std::numeric_limits<float>::quiet_NaN();
  refuses([&]{bind_source_text(bad_second,invalid,1,cancelled);},"nonfinite second source admitted");
  check(bad_second.opens==0,"inference started before all source validation");
  invalid=pcm;invalid[1].pop_back();refuses([&]{bind_source_text(bad_second,invalid,1,cancelled);},"truncated source admitted");
  Fake late_bad;late_bad.alter=[](RecognizedSegment& row){if(row.evidence.front()<0)row.evidence[0]*=-1;};
  refuses([&]{bind_source_text(late_bad,pcm,1,cancelled);},"first result escaped second-source validation");
  check(late_bad.opens==2&&late_bad.resets==2,"second-source refusal did not retire both calls");
  for(size_t failed_open=1;failed_open<=2;++failed_open) {
    Fake partial_open;
    partial_open.on_open=[failed_open](Fake& f){
      if(f.opens==failed_open){f.pcm.assign(32000,.75f);throw std::runtime_error("partial initialization");}
    };
    refuses([&]{bind_source_text(partial_open,pcm,1,cancelled);},"failed source open escaped");
    check(partial_open.resets==failed_open&&partial_open.pcm.empty(),"failed source open retained state");
    partial_open.on_open={};
    check(bind_source_text(partial_open,pcm,2,cancelled).size()==2,"failed open poisoned recovery");
  }
  auto raw=pcm;for(auto& channel:raw)for(auto& x:channel)x*=75;
  const auto normalized=normalize_sources(pcm[0],raw);
  check(std::abs(normalized[0][0]-.125f)<1e-7&&std::abs(normalized[1][0]+.125f)<1e-7,"scale-invariant normalization");
  for(auto& channel:raw)std::fill(channel.begin(),channel.end(),std::numeric_limits<float>::max());
  check(normalize_sources(pcm[0],raw)[0][0]==.125f,"large finite output overflowed");
  check(normalize_sources(std::vector<float>(32000),raw)[0][0]==0,"silent input manufactured energy");
  raw[1][0]=std::numeric_limits<float>::infinity();
  refuses([&]{normalize_sources(pcm[0],raw);},"invalid second normalization channel escaped");
  std::cout<<"source binding contracts passed\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
