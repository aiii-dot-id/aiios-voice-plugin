#pragma once
#include "corrections.h"
#include "worker_json.h"
namespace aii::voice::wire {
// The stored correction list as it travels beside a session's settings:
//   {"schema":"aiii.voice.corrections","revision":N,"rules":[{"heard":"..","meant":".."},..]}
// Exactly these members. A document this engine cannot hold is refused whole;
// it is never applied in part.
struct CorrectionDocument { uint64_t revision=0; Corrections list; };
inline CorrectionDocument read_corrections(const cJSON* j) {
  require(cJSON_IsObject(j) && cJSON_GetArraySize(j)==3,"correction document fields differ");
  require(str(field(j,"schema"),64)=="aiii.voice.corrections","correction document schema differs");
  CorrectionDocument out; out.revision=integer(field(j,"revision"));
  const auto* rows=field(j,"rules");
  require(cJSON_IsArray(rows) && size_t(cJSON_GetArraySize(rows))<=Corrections::max_rules,"correction rules must be a list of at most 64");
  std::vector<Correction> rules;
  for(const auto* row=rows->child;row;row=row->next) {
    require(cJSON_IsObject(row) && cJSON_GetArraySize(row)==2,"correction rule fields differ");
    rules.push_back({str(field(row,"heard"),Corrections::max_bytes),str(field(row,"meant"),Corrections::max_bytes)});
  }
  try { out.list.replace(std::move(rules)); }
  catch(const std::invalid_argument& e) { throw Refused(e.what()); }
  return out;
}
}
