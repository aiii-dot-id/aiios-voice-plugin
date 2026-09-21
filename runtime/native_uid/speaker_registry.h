#pragma once
#include "snapshot.h"
#include <optional>

namespace aii::uid {
struct Association {
  uint64_t revision=0;
  std::string label,external_id;
};
struct SpeakerBucket {
  std::string uuid;
  uint64_t created_revision=0;
  // Empty history is anonymous. Previous associations remain inspectable.
  std::vector<Association> associations;
};
struct SpeakerRegistry {
  uint64_t revision=0;
  Snapshot profiles;
  std::vector<SpeakerBucket> buckets;
};
struct RegistryChange {
  std::string base_sha256,document,uuid,continuity,reason;
  uint64_t revision=0;
};
// Private bounded codec. Never a transcript store. The existing model-bound
// enrollment codec owns embedding validation; legacy enrollments are NOT read
// as anonymous profiles or migrated automatically.
SpeakerRegistry read_registry(const std::string&,const PolicyDocument&);
std::string write_registry(const SpeakerRegistry&,const PolicyDocument&);
// random_uuid is minted by the composition's OS entropy owner, not by a caller,
// speaker label, transcript, track index or a voice-derived hash.
// verified_sample may be supplied ONLY after speaker-specific evidence passed
// the composition's acoustic validation. A missing sample creates a provisional
// bucket, never a guessed match. No automatic profile adaptation/merging.
RegistryChange observe_speaker(const std::string&,const PolicyDocument&,
    uint64_t expected_revision,const std::string& random_uuid,
    const std::optional<Sample>& verified_sample);
// No microphone, inference or live session is needed. The caller applies the
// existing confirmed-write policy and publishes with base_sha256 CAS/readback.
RegistryChange associate_speaker(const std::string&,const PolicyDocument&,
    uint64_t expected_revision,const std::string& uuid,
    const std::string& label,const std::string& external_id);
// Explicit confirmed retention action; removes the selected profile/metadata,
// never rewrites transcripts or reassigns that UUID to another person.
RegistryChange forget_speaker(const std::string&,const PolicyDocument&,
    uint64_t expected_revision,const std::string& uuid);
}
