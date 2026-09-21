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
  if(const auto* uuid=field(detail.get(),"speaker_uuid")) {
    const auto value=str(uuid,36);
    require(value.size()==36 && value[8]=='-' && value[13]=='-' && value[18]=='-' && value[23]=='-' &&
      value[14]=='4' && std::string("89ab").find(value[19])!=std::string::npos,"invalid anonymous speaker UUID");
    for(size_t i=0;i<value.size();++i)if(i!=8&&i!=13&&i!=18&&i!=23)
      require(std::string("0123456789abcdef").find(value[i])!=std::string::npos,"invalid anonymous speaker UUID");
    const auto revision=str(field(detail.get(),"registry_revision"),16);
    require(!revision.empty()&&revision[0]!='0'&&revision.find_first_not_of("0123456789")==std::string::npos &&
      std::stoull(revision)<=9007199254740991ULL,"invalid speaker registry revision");
    const auto continuity=str(field(detail.get(),"continuity"),32);
    require(!known&&(continuity=="matched"||continuity=="new_profile"||continuity=="provisional"),"anonymous speaker continuity invalid");
    put(data,"speaker_uuid",string(value));put(data,"registry_revision",string(revision));put(data,"continuity",string(continuity));
    if(field(detail.get(),"display_label"))put(data,"display_label",string(str(field(detail.get(),"display_label"),512)));
  }
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
