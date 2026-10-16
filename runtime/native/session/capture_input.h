#pragma once
#include "worker_json.h"
#include <algorithm>
#include <optional>
#include <vector>

namespace aii::voice {
// Explicit host-owned capture mode only. No conversation/VAD segmentation,
// ambient final selection, profile access, inference, padding or truncation.
class CaptureInput {
  std::vector<float> pcm_;
  std::optional<uint64_t> cutoff_;
  bool ended_=false, taken_=false;
public:
  std::string request_id;
  uint64_t created_ms=0;
  explicit CaptureInput(const cJSON* args) {
    using namespace wire;
    require(cJSON_IsObject(args),"enrollment_capture object required");
    size_t fields=0;for(auto* f=args->child;f;f=f->next)++fields;
    require(fields==3&&flag(field(args,"consented")),"explicit capture consent and exact metadata required");
    request_id=str(field(args,"request_id"),64);
    require(request_id.size()==64&&request_id.find_first_not_of("0123456789abcdef")==std::string::npos,
        "capture request identity invalid");
    created_ms=integer(field(args,"created_ms"),9007199254740991ULL);
    require(created_ms>0,"trusted capture time required");
  }
  uint64_t received() const {return pcm_.size();}
  const std::optional<uint64_t>& cutoff() const {return cutoff_;}
  bool ended() const {return ended_;}
  void abandon() {
    std::fill(pcm_.begin(),pcm_.end(),0.f);
    std::vector<float>().swap(pcm_);taken_=true;
  }
  void finish(uint64_t end) {
    wire::require(end>=31920&&end<=480000&&end>=received()&&(!cutoff_||*cutoff_==end),
        "capture cutoff differs or is outside 1.995–30 seconds");
    cutoff_=end;
  }
  void feed(uint64_t start,const std::vector<float>& pcm) {
    wire::require(!ended_&&!taken_&&!pcm.empty()&&start==received()&&
        pcm.size()<=480000-received()&&(!cutoff_||pcm.size()<=*cutoff_-received()),
        "capture audio exceeds its exact bounded span");
    pcm_.insert(pcm_.end(),pcm.begin(),pcm.end());
  }
  void end(uint64_t end) {
    wire::require(!ended_&&end==received(),"capture END differs from received samples");
    finish(end);ended_=true;
  }
  std::vector<float> take() {
    wire::require(ended_&&!taken_,"complete capture may be prepared only once");
    taken_=true;return std::move(pcm_);
  }
};
}
