#pragma once
#include "snapshot.h"

namespace aii::uid {
// Private explicitly requested evidence, NOT enrolled identity or ambient
// SessionEvidence. No clock/session expiry and no eviction. The composition
// root supplies consent and trusted capture metadata; the host persists bytes
// with CAS + durable readback. These functions never authorize, infer or do IO.
struct PendingCapture {
  std::string id, request_id, embedding_binding;
  uint64_t created_ms=0, samples=0;
  Sample recording;
};
struct CaptureSet {uint64_t revision=0;std::vector<PendingCapture> captures;};
struct PreparedCaptureSet {std::string base_sha256,snapshot;uint64_t revision;};
constexpr size_t pending_capture_capacity=16,pending_capture_max_bytes=65536;
PendingCapture make_pending_capture(const std::string& request_id,uint64_t created_ms,
    uint64_t samples,const std::string& embedding_binding,Sample);
CaptureSet read_captures(const std::string&,const std::string& expected_binding);
std::string write_captures(const CaptureSet&,const std::string& expected_binding);
PreparedCaptureSet retain_capture(const std::string& current,const PendingCapture&,
    const std::string& expected_binding);
PreparedCaptureSet discard_capture(const std::string& current,const std::string& id,
    const std::string& expected_binding);
PendingCapture select_capture(const CaptureSet&,const std::string& id);
// Deliberately excludes audio, vectors and nonce. A list conveys availability,
// not biometric quality, enrollment readiness or command authority.
struct CaptureInfo {std::string id;uint64_t created_ms,samples;};
std::vector<CaptureInfo> list_captures(const CaptureSet&);
}
