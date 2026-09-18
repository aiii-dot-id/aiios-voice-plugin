#pragma once
#include "snapshot_bridge.h"
#include "../../native_uid/bound_policies.h"
#include "../../native_uid/pending_captures.h"

namespace aii::voice {
// Recovery never admits old embeddings to matching. It preserves exact bytes
// through the same broker/CAS owner, then starts an explicitly empty store.
struct UIDInspection {
  bool profile_absent=false,captures_absent=false;
  std::string profile,captures,profile_state,captures_state,profile_model;
  std::string profile_hash() const;
  std::string captures_hash() const;
  bool needs_recovery() const;
  wire::Json report() const;
};
UIDInspection inspect_uid(SnapshotBridge&,const aii::uid::BoundPolicies&);
wire::Json recover_uid(SnapshotBridge&,const aii::uid::BoundPolicies&,
    const std::string& expected_profile,const std::string& expected_captures,
    const std::string& upload);
}
