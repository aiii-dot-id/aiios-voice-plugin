#pragma once
#include "session.h"
#include "../../native_uid/identity.h"
#include "../../native_uid/enrollment.h"
#include <functional>
#include <optional>

namespace aii::voice {
class NativeSpeaker final:public SpeakerIdentifier {
 public:
  using ReadSnapshot=std::function<aii::uid::Snapshot()>;
  NativeSpeaker(const std::string& verified_model,const std::string& backend,
                aii::uid::Policy,ReadSnapshot,std::optional<aii::uid::Policy> previous = {});
  ~NativeSpeaker() override;
  void open() override;
  void warm();
  aii::uid::Sample prepare_capture(const std::vector<float>&);
  std::string identify(uint64_t,const std::vector<float>&) override;
  using ObserveTrack=std::function<std::string(const std::optional<aii::uid::Sample>&,size_t)>;
  void track_observer(ObserveTrack);
  using ObserveTrackAt=std::function<std::string(uint64_t,uint64_t,const std::optional<aii::uid::Sample>&,size_t)>;
  void track_observer_at(ObserveTrackAt);
  std::string identify_track(uint64_t,const std::vector<float>&) override;
  std::string identify_track_at(uint64_t,uint64_t,const std::vector<float>&) override;
  aii::uid::PreparedEnrollment prepare_enrollment(const std::string& current,
      const aii::uid::PolicyDocument&,const std::string& speaker_id,
      const std::string& label,const std::vector<std::vector<float>>& recordings);
  aii::uid::PreparedEnrollment prepare_selected(const std::string& current,
      const aii::uid::PolicyDocument&,const std::string& speaker_id,
      const std::string& label,const std::vector<uint64_t>& finals);
  std::vector<uint64_t> enrollment_finals();
  void cancel() noexcept override;
 private:
  aii::uid::Sample recording(const std::vector<float>&);
  struct Impl;
  std::unique_ptr<Impl> p_;
};
}
