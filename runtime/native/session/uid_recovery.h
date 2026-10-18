#pragma once
#include "snapshot_bridge.h"
#include "../../native_uid/bound_policies.h"
#include "../../native_uid/pending_captures.h"

namespace aii::voice {
struct UIDInspection {
  bool profile_absent=false,captures_absent=false,registry_absent=false;
  std::string profile,captures,registry,profile_state,captures_state,registry_state,profile_model,registry_model;
  std::string profile_hash() const;
  std::string captures_hash() const;
  std::string registry_hash() const;
  bool registry_recovery() const;
  bool needs_recovery() const;
  wire::Json report() const;
};
UIDInspection inspect_uid(SnapshotBridge&,const aii::uid::BoundPolicies&);
wire::Json recover_uid(SnapshotBridge&,const aii::uid::BoundPolicies&,
    const std::string& expected_profile,const std::string& expected_captures,
    const std::string& upload,const std::string& expected_registry={});
}
