#pragma once
#include <array>
#include <cstdint>
#include <vector>
#include <stdexcept>

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
  SpeakerEvidence(size_t channels=4,uint64_t cadence=hop):channels_(channels),cadence_(cadence) {
    if(!((channels==4 && cadence==1280)||(channels==8 && cadence==160)))
      throw std::invalid_argument("speaker evidence model geometry");
  }
  void push(uint64_t first_frame,const std::vector<float>& probabilities);
  std::vector<EvidenceSpan> finish(uint64_t actual_samples);
  size_t retained_spans() const;
  std::vector<EvidenceSpan> spans() const;
  // Guarded clean regions completed by the most recent push/finish, consumed
  // once by the audio owner. Individually short regions are not voiceprints.
  const std::vector<EvidenceSpan>& regions() const {return regions_;}
  // Selection diagnostics only; these never relax admission or name a person.
  struct Activity {
    // Uncertain competition is distinct from uncertainty about this track
    // alone. Both confident and possible competition veto whole-final UID.
    uint64_t active=0, overlap=0, uncertain=0, competing_uncertain=0;
  };
  const Activity& activity(size_t track) const {return activity_.at(track);}
 private:
  struct Run {uint64_t start=0,end=0,active=0,last_active_end=0;bool open=false;};
  std::array<Run,8> runs_{};
  std::array<EvidenceSpan,8> best_{};
  std::array<Activity,8> activity_{};
  std::vector<EvidenceSpan> regions_;
  uint64_t frames_=0;
  bool finished_=false;
  size_t channels_;
  uint64_t cadence_;
  void close(size_t track,uint64_t actual_samples);
};
}
