#include "capture_enrollment.h"
#include "../vendor/picosha2/picosha2.h"

namespace aii::voice {
namespace {
using namespace wire;
using Store=SnapshotBridge::Store;
std::string hash(const std::string& value){return picosha2::hash256_hex_string(value);}
void upload_id(const std::string& value){require(value.size()==64&&
    value.find_first_not_of("0123456789abcdef")==std::string::npos,"capture upload identity invalid");}
// Use the existing enrollment preparation/normalization, not a second vector
// normalization rule. Exact prior evidence can be reconciled, never relabelled.
std::string candidate(const std::string& current,const uid::PolicyDocument& policy,
    const uid::PendingCapture& capture,const std::string& speaker,const std::string& label,bool& reconciled) {
  const auto blank=uid::write_snapshot({policy.policy,0,{}},policy);
  const auto one=uid::prepare_enrollment(blank,policy,speaker,label,{capture.recording},capture.embedding_binding);
  const auto normalized=uid::read_snapshot(one.snapshot,policy).speakers.at(0).samples.at(0);
  const auto before=uid::read_snapshot(current,policy);
  for(const auto& owner:before.speakers)for(const auto& evidence:owner.samples) {
    if(evidence.audio_sha256!=capture.recording.audio_sha256)continue;
    require(owner.id==speaker&&owner.label==label&&
        uid::encode_vector(evidence.embedding)==uid::encode_vector(normalized.embedding),
        "pending capture conflicts with already enrolled identity/evidence");
    reconciled=true;return current;
  }
  return uid::prepare_enrollment(current,policy,speaker,label,{capture.recording},capture.embedding_binding).snapshot;
}
}
CaptureEnrollment::CaptureEnrollment(SnapshotBridge& bridge,const uid::PolicyDocument& policy)
    :bridge_(bridge),policy_(uid::read_policy(policy.canonical)) {
  require(policy_.policy.minimum_enrollment_samples==1,"verified guided-enrollment policy required; no implicit sample-floor change");
}
std::string CaptureEnrollment::captures(bool& absent) {
  auto value=bridge_.read(&absent,Store::PendingCaptures);
  if(absent)value=uid::write_captures({},policy_.policy.embedding_binding);
  (void)uid::read_captures(value,policy_.policy.embedding_binding);
  return value;
}
std::vector<uid::CaptureInfo> CaptureEnrollment::list() {
  bool absent=false;
  return uid::list_captures(uid::read_captures(captures(absent),policy_.policy.embedding_binding));
}
wire::Json CaptureEnrollment::retain(const uid::PendingCapture& capture,const std::string& upload) {
  upload_id(upload);
  bool absent=false;const auto current=captures(absent);
  const auto next=uid::retain_capture(current,capture,policy_.policy.embedding_binding);
  // Even an exact explicit retry obtains a fresh durability/readback receipt;
  // bytes merely existing after an uncertain write do not prove durability.
  return bridge_.publish(next.snapshot,absent?"":hash(current),absent,upload,Store::PendingCaptures);
}
wire::Json CaptureEnrollment::discard(const std::string& id,const std::string& upload) {
  upload_id(upload);
  bool absent=false;const auto current=captures(absent);
  const auto next=uid::discard_capture(current,id,policy_.policy.embedding_binding);
  return bridge_.publish(next.snapshot,absent?"":hash(current),absent,upload,Store::PendingCaptures);
}
CaptureConfirmation CaptureEnrollment::confirm(const std::string& id,const std::string& speaker,
    const std::string& label,const std::string& upload) {
  upload_id(upload);
  bool captures_absent=false;const auto pending=captures(captures_absent);
  const auto selected=uid::select_capture(uid::read_captures(pending,policy_.policy.embedding_binding),id);
  bool profile_absent=false;auto current=bridge_.read(&profile_absent,Store::Enrollment);
  if(profile_absent)current=uid::write_snapshot({policy_.policy,0,{}},policy_);
  CaptureConfirmation result;
  const auto next=candidate(current,policy_,selected,speaker,label,result.reconciled);
  result.revision=uid::read_snapshot(next,policy_).revision;result.enrollment_sha256=hash(next);
  // Profile first, pending evidence second. A missing/failed receipt or failed
  // readback throws before retirement. The two CAS operations are not atomic.
  // Explicit reconciliation republishes IDENTICAL profile bytes, preserving its
  // revision, to establish durability before cleaning up a prior partial commit.
  result.enrollment_publication=bridge_.publish(next,profile_absent?"":hash(current),profile_absent,
      hash(upload+"/enrollment"),Store::Enrollment);
  result.enrollment_durable=flag(field(result.enrollment_publication.get(),"durable"))&&
      flag(field(result.enrollment_publication.get(),"readback_verified"));
  if(!result.enrollment_durable) {
    result.cleanup_detail="Enrollment publication durability unresolved; pending capture preserved. Reconcile before any retry.";
    return result;
  }
  try {
    const auto retired=uid::discard_capture(pending,id,policy_.policy.embedding_binding);
    result.capture_publication=bridge_.publish(retired.snapshot,hash(pending),false,
        hash(upload+"/retire-capture"),Store::PendingCaptures);
    result.capture_retirement_durable=flag(field(result.capture_publication.get(),"durable"))&&
        flag(field(result.capture_publication.get(),"readback_verified"));
    if(!result.capture_retirement_durable)
      result.cleanup_detail="Speaker is durably enrolled; pending-capture retirement durability is unresolved.";
  }catch(const std::exception& error) {
    // Do not turn a committed enrollment into a reported unchanged profile.
    result.cleanup_detail=std::string("Speaker is durably enrolled; pending-capture retirement unresolved: ")+error.what();
  }
  return result;
}
}
