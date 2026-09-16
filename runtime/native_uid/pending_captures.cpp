#include "pending_captures.h"
#include "../native/session/worker_json.h"
#include "../native/vendor/picosha2/picosha2.h"
#include <algorithm>
#include <set>

namespace aii::uid {
namespace {
using namespace aii::voice::wire;
constexpr uint64_t exact_integer=9007199254740991ULL;
void digest(const std::string& s) {require(s.size()==64&&s.find_first_not_of("0123456789abcdef")==std::string::npos,"capture digest/nonce invalid");}
void fields(const cJSON* j,std::initializer_list<const char*> names) {
  require(cJSON_IsObject(j),"capture object required");size_t n=0;
  for(auto* item=j->child;item;item=item->next)++n;
  require(n==names.size(),"capture fields differ");
  for(auto* name:names)require(field(j,name),"capture field missing");
}
std::string payload(const PendingCapture& c) {
  digest(c.request_id);digest(c.embedding_binding);digest(c.recording.audio_sha256);
  require(c.created_ms>0&&c.created_ms<=exact_integer,"capture timestamp invalid");
  require(c.samples>=31920&&c.samples<=480000,"capture must retain complete bounded context");
  double norm=0;
  for(double value:c.recording.embedding){require(std::isfinite(value),"capture embedding not finite");norm+=value*value;}
  require(std::isfinite(norm)&&std::abs(norm-1)<=1e-6,"capture embedding is not a unit vector");
  return "{\"audio_sha256\":\""+c.recording.audio_sha256+"\",\"created_ms\":"+std::to_string(c.created_ms)+
    ",\"embedding_binding\":\""+c.embedding_binding+"\",\"embedding_f64le_b64\":\""+encode_vector(c.recording.embedding)+
    "\",\"request_id\":\""+c.request_id+"\",\"samples\":"+std::to_string(c.samples)+"}";
}
std::string record(const PendingCapture& c,const std::string& binding) {
  const auto body=payload(c);digest(c.id);
  require(c.embedding_binding==binding,"pending capture model/frontend binding differs");
  require(c.id==picosha2::hash256_hex_string("aiii.uid.pending-capture\n"+body),"pending capture identity/evidence changed");
  return "{\"id\":\""+c.id+"\",\"recording\":"+body+"}";
}
void advance(CaptureSet& set){require(set.revision<exact_integer,"capture revision exhausted");++set.revision;}
PreparedCaptureSet finish(const std::string& base,const CaptureSet& set,const std::string& binding){
  return {picosha2::hash256_hex_string(base),write_captures(set,binding),set.revision};
}
}
PendingCapture make_pending_capture(const std::string& request,uint64_t at,uint64_t samples,
    const std::string& binding,Sample sample) {
  PendingCapture result{"",request,binding,at,samples,std::move(sample)};
  result.id=picosha2::hash256_hex_string("aiii.uid.pending-capture\n"+payload(result));return result;
}
std::string write_captures(const CaptureSet& set,const std::string& binding){
  digest(binding);require(set.revision<=exact_integer&&set.captures.size()<=pending_capture_capacity,"pending capture capacity/revision exceeded");
  std::string out="{\"captures\":[",previous;std::set<std::string> requests,audio;bool first=true;
  for(const auto& capture:set.captures){
    require(previous.empty()||previous<capture.id,"pending captures must be unique and sorted");previous=capture.id;
    require(requests.insert(capture.request_id).second,"capture request already has evidence");
    require(audio.insert(capture.recording.audio_sha256).second,"duplicate capture recording");
    if(!first)out+=',';first=false;out+=record(capture,binding);
  }
  out+="],\"revision\":"+std::to_string(set.revision)+"}";
  require(out.size()<=pending_capture_max_bytes,"pending capture byte capacity exceeded");return out;
}
CaptureSet read_captures(const std::string& raw,const std::string& binding){
  require(!raw.empty()&&raw.size()<=pending_capture_max_bytes,"pending capture byte bound");
  const auto json=parse(raw);fields(json.get(),{"captures","revision"});
  CaptureSet set{integer(field(json.get(),"revision")),{}};
  const auto* items=field(json.get(),"captures");
  require(cJSON_IsArray(items)&&cJSON_GetArraySize(items)<=int(pending_capture_capacity),"pending capture capacity exceeded");
  for(auto* item=items->child;item;item=item->next){
    fields(item,{"id","recording"});const auto* r=field(item,"recording");
    fields(r,{"audio_sha256","created_ms","embedding_binding","embedding_f64le_b64","request_id","samples"});
    PendingCapture capture{str(field(item,"id"),64),str(field(r,"request_id"),64),str(field(r,"embedding_binding"),64),
      integer(field(r,"created_ms")),integer(field(r,"samples"),480000),
      {str(field(r,"audio_sha256"),64),decode_vector(str(field(r,"embedding_f64le_b64"),2732))}};
    set.captures.push_back(std::move(capture));
  }
  require(write_captures(set,binding)==raw,"pending captures not canonical");return set;
}
PreparedCaptureSet retain_capture(const std::string& current,const PendingCapture& capture,const std::string& binding){
  auto set=read_captures(current,binding);(void)record(capture,binding);
  for(const auto& prior:set.captures){
    if(prior.id==capture.id)return finish(current,set,binding); // exact id binds every evidence byte
    require(prior.request_id!=capture.request_id,"capture request already has different evidence");
    require(prior.recording.audio_sha256!=capture.recording.audio_sha256,"duplicate capture recording");
  }
  require(set.captures.size()<pending_capture_capacity,"pending capture store full; confirm or discard an existing capture");
  set.captures.push_back(capture);std::sort(set.captures.begin(),set.captures.end(),[](const auto& a,const auto& b){return a.id<b.id;});
  advance(set);return finish(current,set,binding);
}
PreparedCaptureSet discard_capture(const std::string& current,const std::string& id,const std::string& binding){
  digest(id);auto set=read_captures(current,binding);
  const auto found=std::find_if(set.captures.begin(),set.captures.end(),[&](const auto& c){return c.id==id;});
  if(found!=set.captures.end()){set.captures.erase(found);advance(set);}return finish(current,set,binding);
}
PendingCapture select_capture(const CaptureSet& set,const std::string& id){
  digest(id);const auto found=std::find_if(set.captures.begin(),set.captures.end(),[&](const auto& c){return c.id==id;});
  require(found!=set.captures.end(),"selected pending capture unavailable; refresh capture list");return *found;
}
std::vector<CaptureInfo> list_captures(const CaptureSet& set){
  std::vector<CaptureInfo> out;for(const auto& c:set.captures)out.push_back({c.id,c.created_ms,c.samples});return out;
}
}
