#pragma once
#include "snapshot_bridge.h"
#include "../../native_uid/pending_captures.h"
#include "../../native_uid/enrollment.h"

namespace aii::voice {
struct CaptureConfirmation {
  bool enrollment_durable=false, capture_retirement_durable=false, reconciled=false;
  uint64_t revision=0;
  std::string enrollment_sha256, cleanup_detail;
  wire::Json enrollment_publication=wire::null(), capture_publication=wire::null();
};
class CaptureEnrollment {
 public:
  CaptureEnrollment(SnapshotBridge&,const uid::PolicyDocument&);
  std::vector<uid::CaptureInfo> list();
  wire::Json retain(const uid::PendingCapture&,const std::string& upload);
  wire::Json discard(const std::string& capture_id,const std::string& upload);
  CaptureConfirmation confirm(const std::string& capture_id,
      const std::string& speaker_id,const std::string& label,const std::string& upload);
 private:
  SnapshotBridge& bridge_;
  uid::PolicyDocument policy_;
  std::string captures(bool& absent);
};
}
