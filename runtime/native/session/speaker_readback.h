#pragma once
#include "worker_json.h"

namespace aii::voice::wire {
inline Json speaker_readback(const cJSON* status) {
  auto out=object();
  for(const auto* name:{"session_id","state_sequence","lifecycle","input","synthesis","playback","attributions"}) {
    require(field(status,name),"speaker session readback missing status");
    put(out,name,clone(field(status,name)));
  }
  auto* input=cJSON_GetObjectItemCaseSensitive(out.get(),"input");
  cJSON_DeleteItemFromObject(input,"processing");
  const auto lifecycle=str(field(out.get(),"lifecycle"));
  if(lifecycle=="closed"||lifecycle=="opening") {
    auto state=string(lifecycle);
    require(cJSON_ReplaceItemInObjectCaseSensitive(input,"state",state.get()),"speaker input state missing");
    state.release();
  }
  return out;
}
}
