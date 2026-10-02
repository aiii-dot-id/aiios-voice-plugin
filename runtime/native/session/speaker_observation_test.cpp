#include "speaker_observation.h"
#include "speaker_readback.h"
#include <iostream>
using namespace aii::voice::wire;

Json evidence(const std::string& outcome, const std::string& id, const std::string& label) {
  auto d = object();
  put(d, "outcome", string(outcome)); put(d, "speaker_id", string(id));
  put(d, "label", string(label)); put(d, "reason", string("fixture"));
  put(d, "score", own(cJSON_CreateNumber(.95)));
  return d;
}
int main() {
  try {
    auto state=parse(R"({"session_id":"opaque-session","state_sequence":42,"lifecycle":"open","input":{"state":"absent","processing":{"private":"not for tools"}},"synthesis":{"state":"running"},"playback":{"state":"unobserved"},"attributions":[],"model_execution":{"private":"not for tools"}})");
    auto readback=speaker_readback(state.get());
    require(str(field(field(readback.get(),"input"),"state"))=="absent"&&
      str(field(field(readback.get(),"synthesis"),"state"))=="running"&&
      !field(readback.get(),"model_execution")&&!field(field(readback.get(),"input"),"processing"),
      "Earbud readback invented capture or exposed unrelated fields");
    cJSON_DeleteItemFromObject(state.get(),"lifecycle");put(state,"lifecycle",string("closed"));
    readback=speaker_readback(state.get());
    require(str(field(field(readback.get(),"input"),"state"))=="closed","closed speech reported accepting input");
    const auto diagnostic=parse(R"({"outcome":"unknown","reason":"below_acceptance_threshold","candidate_count":1,"candidate_uuid":"12345678-1234-4234-8234-123456789abc","score":0.46,"threshold":0.56,"minimum_margin":0.105,"profile_revision":"1","policy_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","embedding_binding":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","evidence_samples":64000})");
    require(field(speaker_match_diagnostic(diagnostic.get()).get(),"score"),"diagnostic score missing");
    for(const std::string damage:{"private_embedding","missing_score","bad_count","bad_revision","bad_score","unknown_reason"}) {
      auto d=clone(diagnostic.get());
      if(damage=="private_embedding")put(d,"embedding",string("must not escape"));
      if(damage=="missing_score")cJSON_DeleteItemFromObject(d.get(),"score");
      if(damage=="bad_count"){cJSON_DeleteItemFromObject(d.get(),"candidate_count");put(d,"candidate_count",number(257));}
      if(damage=="bad_revision"){cJSON_DeleteItemFromObject(d.get(),"profile_revision");put(d,"profile_revision",string("01"));}
      if(damage=="bad_score"){cJSON_DeleteItemFromObject(d.get(),"score");put(d,"score",number(2));}
      if(damage=="unknown_reason"){cJSON_DeleteItemFromObject(d.get(),"reason");put(d,"reason",string("invented_reason"));}
      bool rejected=false;try{speaker_match_diagnostic(d.get());}catch(const std::exception&){rejected=true;}
      require(rejected,"malformed or private diagnostic escaped");
    }
    auto event = speaker_observation(evidence("known", "person-237", "Sam"), 17);
    require(str(field(event.get(), "speaker_id")) == "person-237",
            "stable enrolled ID missing or replaced by label");
    require(str(field(event.get(), "speaker")) == "Sam", "display label lost");
    require(integer(field(event.get(), "refers_to")) == 17 &&
                !flag(field(event.get(), "used_for_permissions")), "UID event changed authority or final binding");
    auto renamed = speaker_observation(evidence("known", "person-237", "New label"), 18);
    auto same_label = speaker_observation(evidence("known", "person-672", "Sam"), 19);
    require(str(field(renamed.get(), "speaker_id")) == "person-237" &&
                str(field(same_label.get(), "speaker_id")) == "person-672",
            "display label changed speaker identity");
    for (const char* outcome : {"unknown", "ambiguous", "unavailable"}) {
      // Even a diagnostic candidate must not escape as an accepted identity.
      auto uncertain = speaker_observation(evidence(outcome, "person-237", "Sam"), 20);
      const auto* id = field(uncertain.get(), "speaker_id");
      const auto* label = field(uncertain.get(), "speaker");
      require(cJSON_IsString(id) && !id->valuestring[0] &&
                  cJSON_IsString(label) && !label->valuestring[0],
              "uncertain match claimed a speaker identity");
    }
    for (const auto& id : {std::string(""), std::string("Sam Smith"), std::string("../237"),
                           std::string("_237"), std::string("237\n"), std::string(97, 'a')}) {
      bool refused = false;
      try { speaker_observation(evidence("known", id, "Sam"), 1); }
      catch (const Refused&) { refused = true; }
      require(refused, "malformed known speaker ID admitted");
    }
    for (const char* damage : {"missing_id", "bad_outcome", "missing_final"}) {
      auto d = evidence("known", "person-237", "Sam"); bool refused = false;
      if (std::string(damage) == "missing_id") cJSON_DeleteItemFromObject(d.get(), "speaker_id");
      if (std::string(damage) == "bad_outcome") d = evidence("guess", "person-237", "Sam");
      try { speaker_observation(std::move(d), std::string(damage) == "missing_final" ? 0 : 1); }
      catch (const Refused&) { refused = true; }
      require(refused, "incomplete speaker identity accepted");
    }
    std::cout << "stable IDs survive rename/collision; uncertain decisions cannot claim IDs\n";
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
