#pragma once
#include "snapshot_bridge.h"
#include "../../native_uid/bound_policies.h"
#include "../../native_uid/pending_captures.h"

namespace aii::voice {
// Recovery never admits old embeddings to matching. It preserves exact bytes
// through the same broker/CAS owner, then starts an explicitly empty store.
struct UIDInspection {
  bool profile_absent=false,captures_absent=false,registry_absent=false;
  std::string profile,captures,registry,profile_state,captures_state,registry_state,profile_model,registry_model;
  std::string profile_hash() const;
  std::string captures_hash() const;
  std::string registry_hash() const;
  // A ready or absent speaker registry is not reported, so a confirmation
  // naming only the enrollment and captures keeps its earlier meaning.
  bool registry_recovery() const;
  bool needs_recovery() const;
  wire::Json report() const;
};
UIDInspection inspect_uid(SnapshotBridge&,const aii::uid::BoundPolicies&);
// expected_registry is empty exactly when the inspection did not report the
// speaker registry; otherwise it is that registry's observed digest.
wire::Json recover_uid(SnapshotBridge&,const aii::uid::BoundPolicies&,
    const std::string& expected_profile,const std::string& expected_captures,
    const std::string& upload,const std::string& expected_registry={});
}
