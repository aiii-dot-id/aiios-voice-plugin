#include "speaker_observation.h"
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
