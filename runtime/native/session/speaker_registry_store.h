#pragma once
#include "snapshot_bridge.h"
#include "../../native_uid/speaker_registry.h"
#include "../../native_uid/profile_admission.h"
#include "../../native_uid/speaker_retention.h"
#include <atomic>
#include <deque>
#include <functional>
#include <set>

namespace aii::voice {
struct SpeakerRetention {
  bool anonymous_profiles=true;
  uint32_t expiry_days=0;
};
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
  void checking(bool on){checking_=on;}
  void retain(SpeakerRetention rule){std::lock_guard<std::mutex> lock(retention_mutex_);retention_=rule;}
  void clock(std::function<uint64_t()> today){std::lock_guard<std::mutex> lock(retention_mutex_);today_=std::move(today);}
  void on_expired(std::function<void(size_t)> said){std::lock_guard<std::mutex> lock(retention_mutex_);said_=std::move(said);}
  static aii_voice_result callback(void*,const aii_voice_capture*,char*,size_t,size_t*) noexcept;
  static aii_voice_result callback_at(void*,uint64_t,uint64_t,const aii_voice_capture*,char*,size_t,size_t*) noexcept;
 private:
  SnapshotBridge& bridge_;
  aii::uid::PolicyDocument policy_;
  std::optional<aii::uid::PolicyDocument> previous_;
  std::mutex mutex_;
  std::atomic<bool> checking_{false};
  std::mutex retention_mutex_;
  std::optional<SpeakerRetention> retention_;
  std::function<uint64_t()> today_;
  std::function<void(size_t)> said_;
  std::optional<SpeakerRetention> retention_now();
  uint64_t today();
  std::string expire(const std::string& raw,bool& absent,const aii::uid::PolicyDocument&,bool observed,
      std::vector<std::string>* expired=nullptr,const aii::uid::Snapshot* enrolled=nullptr);
  aii::uid::HeardDays load_heard(std::string& raw,bool& absent);
  void save_heard(const std::string& raw,bool absent,const aii::uid::HeardDays&,bool observed);
  void heard_now(const aii::uid::SpeakerRegistry&,const std::string& uuid,const aii::uid::Snapshot* enrolled);
  aii::uid::ProfileAdmissions admissions_;
  uint64_t track_session_=0,track_utterance_=0;
  std::set<std::string> assigned_tracks_;
  struct Unobserved { uint64_t session,utterance; aii_voice_capture evidence; };
  struct Unpublished { std::string base; aii::uid::RegistryChange change; bool absent; };
  static constexpr size_t kept_limit=4;
  std::deque<Unobserved> unobserved_;
  std::deque<Unpublished> unpublished_;
  bool publication_kept_=false;
  wire::Json observe_now(const aii_voice_capture*,uint64_t session,uint64_t utterance);
  void finish_kept();
  std::string read(bool&);
  const aii::uid::PolicyDocument& policy_for(const std::string&) const;
  aii::uid::Snapshot read_enrolled();
  wire::Json project(const std::string&,const std::vector<std::string>& expired={});
  void publish(const std::string&,const aii::uid::RegistryChange&,bool);
  void publish_observed(const std::string&,const aii::uid::RegistryChange&,bool);
 public:
  size_t kept_observations();
  size_t kept_publications();
};
}
