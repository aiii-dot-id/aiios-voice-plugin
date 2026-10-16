#pragma once
#include "worker_json.h"

namespace aii::voice::wire {
// Only bounded acoustic decision metadata crosses this seam. In particular,
// no future private field is exposed merely because the engine added it.
inline Json speaker_match_diagnostic(const cJSON* value) {
  require(cJSON_IsObject(value),"speaker match diagnostic must be an object");
  auto out=object();
  for(const auto* item=value->child;item;item=item->next) {
    const std::string key=item->string?item->string:"";
    require(!field(out.get(),key.c_str()),"duplicate speaker match diagnostic field");
    if(key=="score"||key=="margin"||key=="threshold"||key=="minimum_margin") {
      const bool similarity=key=="score"||key=="threshold";
      require(cJSON_IsNumber(item)&&std::isfinite(item->valuedouble)&&
        item->valuedouble>=(similarity?-1:0)&&item->valuedouble<=(similarity?1:2),"speaker match metric invalid");
    } else if(key=="candidate_count") {
      require(integer(item)<=256,"speaker candidate count invalid");
    } else if(key=="evidence_samples") {
      const auto n=integer(item);require(n>=31920&&n<=160000,"speaker evidence sample count invalid");
    } else if(key=="candidate_uuid") {
      const auto id=str(item,36);
      require(id.size()==36&&id[8]=='-'&&id[13]=='-'&&id[18]=='-'&&id[23]=='-'&&id[14]=='4'&&
        std::string("89ab").find(id[19])!=std::string::npos,"speaker match candidate invalid");
      for(size_t i=0;i<id.size();++i)if(i!=8&&i!=13&&i!=18&&i!=23)
        require(std::string("0123456789abcdef").find(id[i])!=std::string::npos,"speaker match candidate invalid");
    } else if(key=="profile_revision") {
      const auto revision=str(item,16);
      require(revision.find_first_not_of("0123456789")==std::string::npos&&
        (revision=="0"||revision[0]!='0')&&std::stoull(revision)<=9007199254740991ULL,"speaker match revision invalid");
    } else if(key=="policy_sha256"||key=="embedding_binding") {
      const auto digest=str(item,64);
      require(digest.size()==64&&digest.find_first_not_of("0123456789abcdef")==std::string::npos,"speaker match binding invalid");
    } else if(key=="outcome") {
      const auto outcome=str(item,16);
      require(outcome=="known"||outcome=="unknown"||outcome=="ambiguous","speaker match outcome invalid");
    } else if(key=="reason") {
      const auto reason=str(item,64);
      require(reason=="accepted"||reason=="no_enrollments"||reason=="below_acceptance_threshold"||
        reason=="insufficient_enrollment"||reason=="insufficient_separation"||
        reason=="anonymous_profile_needs_corroboration","speaker match reason invalid");
    } else throw std::invalid_argument("unknown speaker match diagnostic field");
    put(out,key.c_str(),clone(item));
  }
  for(const auto* key:{"outcome","reason","candidate_count","threshold","minimum_margin",
      "profile_revision","policy_sha256","embedding_binding","evidence_samples"})
    require(field(out.get(),key),"speaker match diagnostic field missing");
  const auto count=integer(field(out.get(),"candidate_count"));
  require(bool(field(out.get(),"score"))==(count>0)&&bool(field(out.get(),"candidate_uuid"))==(count>0)&&
    bool(field(out.get(),"margin"))==(count>1),"speaker match candidate metrics incomplete");
  return out;
}
// The label is presentation; UUID is the common key and enrolled ID is legacy
// compatibility metadata. The host owns its filter policy, never this mapper.
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
      value[14]=='4' && std::string("89ab").find(value[19])!=std::string::npos,"invalid speaker UUID");
    for(size_t i=0;i<value.size();++i)if(i!=8&&i!=13&&i!=18&&i!=23)
      require(std::string("0123456789abcdef").find(value[i])!=std::string::npos,"invalid speaker UUID");
    const auto revision=str(field(detail.get(),"registry_revision"),16);
    require(!revision.empty()&&revision[0]!='0'&&revision.find_first_not_of("0123456789")==std::string::npos &&
      std::stoull(revision)<=9007199254740991ULL,"invalid speaker registry revision");
    const auto continuity=str(field(detail.get(),"continuity"),32);
    require((!known&&(continuity=="matched"||continuity=="new_profile"||continuity=="provisional"))||
      (known&&continuity=="matched"),"speaker continuity invalid");
    put(data,"speaker_uuid",string(value));put(data,"registry_revision",string(revision));put(data,"continuity",string(continuity));
    if(field(detail.get(),"display_label"))put(data,"display_label",string(str(field(detail.get(),"display_label"),512)));
  }
  // A rejected comparison still has useful diagnostics, but no accepted UUID.
  if(const auto* match=field(detail.get(),"match"))put(data,"match",speaker_match_diagnostic(match));
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
