#pragma once
#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace aii::uid {
using Vector = std::array<double,256>;
std::vector<uint32_t> unicode_scalars(const std::string&); // strict UTF-8
// A read-only projection of speaker_identity/snapshot.py, NOT a new store.
// The composition root supplies a verified policy from its bound catalog and
// the authoritative host snapshot. No enrollment, file IO or authorization.
struct Policy {
  std::string embedding_binding, calibration_sha256, fingerprint;
  double threshold=.56, minimum_margin=.105;
  unsigned minimum_enrollment_samples=3;
};
struct Sample { std::string audio_sha256; Vector embedding; };
struct Speaker { std::string id,label; std::vector<Sample> samples; };
struct Snapshot { Policy policy; uint64_t revision=0; std::vector<Speaker> speakers; };
struct Decision {
  std::string outcome, speaker_id, label, reason;
  std::optional<double> score,margin;
  uint64_t enrollment_revision=0;
  std::string policy_sha256;
};
void validate(const Snapshot&, const Policy& expected);
Decision identify(const Snapshot&,const Policy& expected,const Vector&,
                  const std::string& embedding_binding);
}
