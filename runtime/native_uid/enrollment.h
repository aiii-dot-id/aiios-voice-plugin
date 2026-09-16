#pragma once
#include "snapshot.h"

namespace aii::uid {
// Pure preparation over the existing canonical enrollment snapshot. These
// functions neither authorize nor publish a change and never alter their input.
// Only the host's CAS publication and readback can make a preparation current.
struct PreparedEnrollment {
  std::string base_sha256, snapshot;
  uint64_t revision;
};
PreparedEnrollment prepare_enrollment(const std::string& current,
    const PolicyDocument&, const std::string& speaker_id, const std::string& label,
    const std::vector<Sample>& recordings, const std::string& embedding_binding);
PreparedEnrollment prepare_removal(const std::string& current,
    const PolicyDocument&, const std::string& speaker_id);
PreparedEnrollment prepare_reset(const std::string& current, const PolicyDocument&);
// Explicit upgrade preparation, never automatic policy substitution on read.
// Only the calibrated three-recording -> single-guided-recording transition
// is admitted, with identical model and matching thresholds. Existing usable
// profiles retain their exact evidence/labels/IDs; incomplete old profiles are
// named and refused, not silently made usable by lowering the sample floor.
// Both policies must come from bound runtime assets, never AI arguments. The
// caller still owes operator confirmation, CAS publication and durable readback.
PreparedEnrollment prepare_guided_policy_transition(const std::string& current,
    const PolicyDocument& previous, const PolicyDocument& guided);
}
