#pragma once
#include <array>
#include <cstdint>
#include <vector>

namespace aii::multitalker {
struct EvidenceSpan {
  uint32_t track=0;
  uint64_t start=0,end=0,active_samples=0;
};
// Selects bounded candidate PCM windows from inferred activity only. Selection
// is not proof of acoustic purity: the composition must qualify this selector
// against overlapping recordings before using it as a UID evidence source.
// Silence can occur inside a window; another or uncertain voice cannot.
class SpeakerEvidence {
 public:
  static constexpr uint64_t hop=1280, guard=2*hop;
  static constexpr uint64_t minimum_active=32000, maximum_span=160000;
  void push(uint64_t first_frame,const std::vector<float>& probabilities);
  std::vector<EvidenceSpan> finish(uint64_t actual_samples);
  size_t retained_spans() const;
 private:
  struct Run {uint64_t start=0,end=0,active=0,last_active_end=0;bool open=false;};
  std::array<Run,4> runs_{};
  std::array<EvidenceSpan,4> best_{};
  uint64_t frames_=0;
  bool finished_=false;
  void close(size_t track,uint64_t actual_samples);
};
}
