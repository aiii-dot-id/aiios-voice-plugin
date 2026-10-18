#pragma once
#include "snapshot.h"

namespace aii::uid {
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
PreparedEnrollment prepare_guided_policy_transition(const std::string& current,
    const PolicyDocument& previous, const PolicyDocument& guided);
PreparedEnrollment prepare_reembedding(const std::string& current,
    const PolicyDocument& previous,const PolicyDocument& target,
    const std::vector<Sample>& regenerated);
}
