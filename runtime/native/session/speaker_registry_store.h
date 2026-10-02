#pragma once
#include "snapshot_bridge.h"
#include "../../native_uid/speaker_registry.h"
#include "../../native_uid/profile_admission.h"
#include <set>

namespace aii::voice {
// Uses the existing private-store broker, not a shadow filesystem. One owner
// serializes read/modify/publish; observations wait for management, while
// concurrent management refuses promptly instead of blocking the control lane.
class SpeakerRegistryStore {
 public:
  SpeakerRegistryStore(SnapshotBridge& bridge,aii::uid::PolicyDocument policy,
      std::optional<aii::uid::PolicyDocument> previous={})
      :bridge_(bridge),policy_(std::move(policy)),previous_(std::move(previous)){}
  wire::Json observe(const aii_voice_capture*,uint64_t session=0,uint64_t utterance=0);
  wire::Json list();
  wire::Json associate(uint64_t,const std::string&,const std::string&,const std::string&);
  wire::Json forget(uint64_t,const std::string&);
  wire::Json link(uint64_t,const std::string&,const std::string&);
  wire::Json upgrade_policy();
  static aii_voice_result callback(void*,const aii_voice_capture*,char*,size_t,size_t*) noexcept;
  static aii_voice_result callback_at(void*,uint64_t,uint64_t,const aii_voice_capture*,char*,size_t,size_t*) noexcept;
 private:
  SnapshotBridge& bridge_;
  aii::uid::PolicyDocument policy_;
  std::optional<aii::uid::PolicyDocument> previous_;
  std::mutex mutex_;
  aii::uid::ProfileAdmissions admissions_;
  uint64_t track_session_=0,track_utterance_=0;
  std::set<std::string> assigned_tracks_;
  std::string read(bool&);
  const aii::uid::PolicyDocument& policy_for(const std::string&) const;
  aii::uid::Snapshot read_enrolled();
  wire::Json project(const std::string&);
  void publish(const std::string&,const aii::uid::RegistryChange&,bool);
};
}
