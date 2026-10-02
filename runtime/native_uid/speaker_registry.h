#pragma once
#include "snapshot.h"
#include "enrollment.h"
#include <optional>
#include <utility>

namespace aii::uid {
constexpr size_t anonymous_profile_minimum_samples=2;
struct Association {
  uint64_t revision=0;
  std::string label,external_id;
};
struct EnrollmentReference {
  std::string id;
  std::vector<std::string> evidence;
};
struct SpeakerBucket {
  std::string uuid;
  uint64_t created_revision=0;
  // Empty history is anonymous. Previous associations remain inspectable.
  std::vector<Association> associations;
  // Confirmed corrections retain the original UUID and profiles. A self-link
  // explicitly undoes a correction; history is never rewritten.
  std::vector<std::pair<uint64_t,std::string>> links{};
  // A persisted random UUID for a legacy enrollment. Evidence anchors prevent
  // recycling a human-readable enrollment ID from inheriting another person.
  std::optional<EnrollmentReference> enrollment{};
};
struct SpeakerRegistry {
  uint64_t revision=0;
  Snapshot profiles;
  std::vector<SpeakerBucket> buckets;
};
struct RegistryChange {
  std::string base_sha256,document,uuid,continuity,reason;
  uint64_t revision=0;
  std::optional<Decision> match;
};
enum class ProfileAdmission { Unconfirmed, Corroborated };
// Private bounded codec. Never a transcript store. The existing model-bound
// enrollment codec owns embedding validation. Enrollment UUID references add
// identity metadata only; legacy embeddings are not copied into anonymous profiles.
SpeakerRegistry read_registry(const std::string&,const PolicyDocument&);
std::string write_registry(const SpeakerRegistry&,const PolicyDocument&);
std::string canonical_speaker(const SpeakerRegistry&,const std::string&);
const Speaker* bound_enrollment(const SpeakerBucket&,const Snapshot&);
RegistryChange bind_enrolled_speaker(const std::string&,const PolicyDocument&,
    uint64_t expected_revision,const std::string& random_uuid,const Speaker&,
    const std::string& existing_uuid={});
// Anonymous recognition uses the same two-recording requirement as admission.
// Older singleton profiles stay in storage/history and in novelty guards, not
// in the accepted-identity competition. Explicit enrollment is separate.
Snapshot anonymous_matching_profiles(const SpeakerRegistry&);
RegistryChange link_speaker(const std::string&,const PolicyDocument&,
    uint64_t expected_revision,const std::string& source,const std::string& target);
// Bounded decision evidence, never embeddings/audio or an authorization claim.
// Absent acoustic evidence yields no match diagnostics rather than zero scores.
std::string match_diagnostics(const RegistryChange&,const PolicyDocument&);
// random_uuid is minted by the composition's OS entropy owner, not by a caller,
// speaker label, transcript, track index or a voice-derived hash.
// verified_sample may be supplied ONLY after speaker-specific evidence passed
// the composition's acoustic validation. Missing or ambiguous evidence returns
// no UUID and leaves the registry byte-identical. The final reference identifies
// this unresolved observation. No automatic profile adaptation/merging.
// Unknown evidence also defaults to no write; Corroborated is an explicit
// composition verdict from independent-utterance matching, not an input field.
RegistryChange observe_speaker(const std::string&,const PolicyDocument&,
    uint64_t expected_revision,const std::string& random_uuid,
    const std::optional<Sample>& verified_sample,
    ProfileAdmission admission=ProfileAdmission::Unconfirmed,
    const std::optional<Sample>& corroborating_sample=std::nullopt,
    const Snapshot* enrolled=nullptr);
// No microphone, inference or live session is needed. The caller applies the
// existing confirmed-write policy and publishes with base_sha256 CAS/readback.
RegistryChange associate_speaker(const std::string&,const PolicyDocument&,
    uint64_t expected_revision,const std::string& uuid,
    const std::string& label,const std::string& external_id);
// Explicit confirmed retention action; removes the selected profile/metadata,
// never rewrites transcripts or reassigns that UUID to another person.
RegistryChange forget_speaker(const std::string&,const PolicyDocument&,
    uint64_t expected_revision,const std::string& uuid);
// Same offline evidence law as prepare_reembedding. Association/link history
// and enrollment references are identity metadata, not embedding coordinates.
RegistryChange prepare_registry_reembedding(const std::string&,
    const PolicyDocument& previous,const PolicyDocument& target,
    uint64_t expected_revision,const std::vector<Sample>& regenerated);
struct PreparedSpeakerModelTransition {
  PreparedEnrollment enrollment;
  RegistryChange registry;
};
// Prepare both documents against one exact evidence set. Shared evidence is
// regenerated once, not separately for enrolled and anonymous representations.
// This performs no publication or activation. The owner must retire sessions,
// verify both base hashes and publish the pair with the model as one operation.
PreparedSpeakerModelTransition prepare_speaker_model_transition(
    const std::string& enrollment,const std::string& registry,
    const PolicyDocument& previous,const PolicyDocument& target,
    uint64_t enrollment_revision,uint64_t registry_revision,
    const std::vector<Sample>& regenerated);
}
