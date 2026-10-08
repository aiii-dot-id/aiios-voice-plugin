#pragma once
#include "snapshot_bridge.h"
#include "../../native_uid/speaker_registry.h"
#include "../../native_uid/profile_admission.h"
#include <deque>
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
  // WHAT LATE STORAGE LEFT UNDONE IS KEPT AND DONE WHEN STORAGE ANSWERS
  // AGAIN. An observation is two things: an answer for its final, which
  // cannot wait and says the storage was late; and what the speaker files
  // learn from the utterance, which can. Before this the second was simply
  // lost with the first: a voice heard while storage was slow taught the
  // files nothing, and a profile whose two recordings had just been matched
  // was not stored and, because a recording is never offered twice as its
  // own confirmation, could not be matched again from the same two.
  //   - the reads were late: the recording itself is kept and observed
  //     again, as the observation it was (its own session and utterance);
  //   - the publication was late: the change is kept and published again
  //     only after reading the file back: taken as done if the file is
  //     already what the change made it, published if the file is still
  //     what the change was made from, and dropped if it has since become
  //     something else. Nothing is published blind.
  // Both are bounded, done after the next observation whose own storage
  // answered, and belong to observations only: an operator's confirmed
  // change that was late is still refused and never retried here.
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
  wire::Json project(const std::string&);
  void publish(const std::string&,const aii::uid::RegistryChange&,bool);
  void publish_observed(const std::string&,const aii::uid::RegistryChange&,bool);
 public:
  // What is kept, for the worker's status and for tests.
  size_t kept_observations();
  size_t kept_publications();
};
}
