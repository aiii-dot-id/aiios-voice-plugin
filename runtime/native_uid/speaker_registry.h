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
  std::vector<Association> associations;
  std::vector<std::pair<uint64_t,std::string>> links{};
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
SpeakerRegistry read_registry(const std::string&,const PolicyDocument&);
std::string write_registry(const SpeakerRegistry&,const PolicyDocument&);
std::string canonical_speaker(const SpeakerRegistry&,const std::string&);
const Speaker* bound_enrollment(const SpeakerBucket&,const Snapshot&);
RegistryChange bind_enrolled_speaker(const std::string&,const PolicyDocument&,
    uint64_t expected_revision,const std::string& random_uuid,const Speaker&,
    const std::string& existing_uuid={});
Snapshot anonymous_matching_profiles(const SpeakerRegistry&);
RegistryChange link_speaker(const std::string&,const PolicyDocument&,
    uint64_t expected_revision,const std::string& source,const std::string& target);
std::string match_diagnostics(const RegistryChange&,const PolicyDocument&);
RegistryChange observe_speaker(const std::string&,const PolicyDocument&,
    uint64_t expected_revision,const std::string& random_uuid,
    const std::optional<Sample>& verified_sample,
    ProfileAdmission admission=ProfileAdmission::Unconfirmed,
    const std::optional<Sample>& corroborating_sample=std::nullopt,
    const Snapshot* enrolled=nullptr);
RegistryChange associate_speaker(const std::string&,const PolicyDocument&,
    uint64_t expected_revision,const std::string& uuid,
    const std::string& label,const std::string& external_id);
RegistryChange forget_speaker(const std::string&,const PolicyDocument&,
    uint64_t expected_revision,const std::string& uuid);
RegistryChange prepare_registry_reembedding(const std::string&,
    const PolicyDocument& previous,const PolicyDocument& target,
    uint64_t expected_revision,const std::vector<Sample>& regenerated);
struct PreparedSpeakerModelTransition {
  PreparedEnrollment enrollment;
  RegistryChange registry;
};
PreparedSpeakerModelTransition prepare_speaker_model_transition(
    const std::string& enrollment,const std::string& registry,
    const PolicyDocument& previous,const PolicyDocument& target,
    uint64_t enrollment_revision,uint64_t registry_revision,
    const std::vector<Sample>& regenerated);
}
