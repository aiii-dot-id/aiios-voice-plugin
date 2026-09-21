#pragma once
#include "snapshot_bridge.h"
#include "../../native_uid/speaker_registry.h"

namespace aii::voice {
// Uses the existing private-store broker, not a shadow filesystem. One owner
// serializes read/modify/publish; concurrent management refuses promptly.
class SpeakerRegistryStore {
 public:
  SpeakerRegistryStore(SnapshotBridge& bridge,aii::uid::PolicyDocument policy)
      :bridge_(bridge),policy_(std::move(policy)){}
  wire::Json observe(const aii_voice_capture*);
  wire::Json list();
  wire::Json associate(uint64_t,const std::string&,const std::string&,const std::string&);
  wire::Json forget(uint64_t,const std::string&);
  static aii_voice_result callback(void*,const aii_voice_capture*,char*,size_t,size_t*) noexcept;
 private:
  SnapshotBridge& bridge_;
  aii::uid::PolicyDocument policy_;
  std::mutex mutex_;
  std::string read(bool&);
  void publish(const std::string&,const aii::uid::RegistryChange&,bool);
};
}
