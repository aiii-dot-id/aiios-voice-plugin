#pragma once
#include "worker_json.h"

namespace aii::voice::wire {
// The label is presentation; only the enrolled ID can key a host UID filter.
// This is evidence about a final, never a permission or operator assertion.
inline Json speaker_observation(Json detail, uint64_t final_sequence) {
  require(final_sequence != 0, "UID observation lacks its public final");
  const auto outcome = str(field(detail.get(), "outcome"));
  const bool known = outcome == "known";
  require(known || outcome == "unknown" || outcome == "ambiguous" ||
              outcome == "unavailable", "UID decision invalid");
  std::string id;
  if (known) {
    id = str(field(detail.get(), "speaker_id"), 96);
    const std::string alnum = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    require(alnum.find(id[0]) != std::string::npos &&
                id.find_first_not_of(alnum + "_.-") == std::string::npos,
            "invalid observed speaker ID");
  }
  auto data = object();
  put(data, "refers_to", number(final_sequence));
  put(data, "decision", string(known ? "known" : outcome == "unknown" ? "unknown" : "uncertain"));
  put(data, "reason", string(str(field(detail.get(), "reason"), 128)));
  put(data, "speaker", string(known ? str(field(detail.get(), "label"), 512) : ""));
  put(data, "speaker_id", string(id));
  if (const auto* score = field(detail.get(), "score"); cJSON_IsNumber(score)) {
    require(std::isfinite(score->valuedouble) && score->valuedouble >= -1 &&
                score->valuedouble <= 1, "UID score invalid");
    put(data, "score", clone(score));
  }
  put(data, "late", boolean(true));
  put(data, "used_for_permissions", boolean(false));
  put(data, "native_evidence", std::move(detail));
  return data;
}
} // namespace aii::voice::wire
