#pragma once
#include "session.h"
#include "../../native_uid/identity.h"
#include "../../native_uid/enrollment.h"
#include <functional>
#include <optional>

namespace aii::voice {
class NativeSpeaker final:public SpeakerIdentifier {
 public:
  // ReadSnapshot runs on the UID worker. It must return a validated host-owned
  // snapshot or throw within the host-call deadline. An unreadable snapshot is
  // NEVER an empty enrollment. It must not wait on the Session control owner.
  using ReadSnapshot=std::function<aii::uid::Snapshot()>;
  NativeSpeaker(const std::string& verified_model,const std::string& backend,
                aii::uid::Policy,ReadSnapshot,std::optional<aii::uid::Policy> previous = {});
  ~NativeSpeaker() override;
  void open() override;
  void warm(); // inference only; never reads or mutates enrollments
  // Idle-model lease only. Completed explicit recording, not live ambient
  // evidence. No profile callback, persistence or enrollment occurs here.
  aii::uid::Sample prepare_capture(const std::vector<float>&);
  std::string identify(uint64_t,const std::vector<float>&) override;
  // Same exclusive UID inference owner as identify; never call concurrently.
  // Selection/authorization and final-span custody belong to the composition
  // root. Produces only candidate bytes: no file write or automatic enrollment.
  aii::uid::PreparedEnrollment prepare_enrollment(const std::string& current,
      const aii::uid::PolicyDocument&,const std::string& speaker_id,
      const std::string& label,const std::vector<std::vector<float>>& recordings);
  // Uses only this open session's retained, already embedded final spans.
  // May run concurrently with identify; never starts a second inference call.
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
