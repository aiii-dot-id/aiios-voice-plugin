#include "snapshot_bridge.h"
#include "../../native_uid/snapshot.h"
#include "../vendor/picosha2/picosha2.h"
#include <algorithm>

namespace aii::voice {
using namespace wire;
using Clock=std::chrono::steady_clock;
static size_t store_limit(SnapshotBridge::Store store) {
  if(store==SnapshotBridge::Store::Enrollment||store==SnapshotBridge::Store::SpeakerRegistry)return 8u<<20;
  if(store==SnapshotBridge::Store::Recovery)return 12u<<20;
  require(store==SnapshotBridge::Store::PendingCaptures,"unknown UID storage resource");return 65536;
}
void SnapshotBridge::begin(const std::string& session) {
  std::lock_guard<std::mutex> l(mutex_);
  require(!busy_&&!pending_,"prior UID read not retired");
  require(bool(send_)&&!session.empty(),"UID snapshot bridge not configured");
  session_=session;live_=true;reply_=null();
}
void SnapshotBridge::cancel() noexcept {
  std::lock_guard<std::mutex> l(mutex_);live_=false;pending_=0;changed_.notify_all();
}
void SnapshotBridge::accept(const cJSON* j) {
  require(cJSON_IsObject(j),"snapshot reply object required");
  const auto id=integer(field(j,"id"));const auto sid=str(field(j,"session_id"),128);
  std::lock_guard<std::mutex> l(mutex_);
  require(issued_.count(id)&&issued_.at(id)==sid,"foreign snapshot reply");
  if(!live_||id!=pending_||sid!=session_)return;
  require(cJSON_IsNull(reply_.get()),"duplicate active snapshot reply");
  reply_=clone(j);changed_.notify_all();
}
Json SnapshotBridge::page(uint64_t offset,bool digest,Clock::time_point deadline,Store store,const std::string& archive) {
  auto q=object();put(q,"offset",number(offset));put(q,"digest",boolean(digest));
  return exchange(std::move(q),deadline,store,archive);
}
Json SnapshotBridge::exchange(Json query,Clock::time_point deadline,Store store,const std::string& archive) {
  (void)store_limit(store);
  require(store==Store::Recovery ? archive.size()==64&&archive.find_first_not_of("0123456789abcdef")==std::string::npos : archive.empty(),"invalid recovery archive identity");
  if(store==Store::Recovery)put(query,"resource",string("recovery:"+archive));
  if(store==Store::PendingCaptures)put(query,"resource",string("captures"));
  if(store==Store::SpeakerRegistry)put(query,"resource",string("speaker_registry"));
  std::unique_lock<std::mutex> l(mutex_);
  require(live_&&Clock::now()<deadline,"UID read cancelled/deadline");
  require(next_<9007199254740991ULL,"UID request IDs exhausted");
  pending_=++next_;issued_[pending_]=session_;
  if(issued_.size()>64)issued_.erase(issued_.begin());
  reply_=null();auto message=object();
  put(query,"id",number(pending_));put(query,"session_id",string(session_));
  put(message,"snapshot_request",std::move(query));
  l.unlock();send_(std::move(message));l.lock();
  const auto limit=std::min(deadline,Clock::now()+std::chrono::seconds(2));
  if(!changed_.wait_until(l,limit,[&]{return !live_||!cJSON_IsNull(reply_.get());})||!live_)
    throw std::runtime_error("UID snapshot read cancelled/deadline");
  pending_=0;return std::move(reply_);
}
void SnapshotBridge::acquire() {
  std::lock_guard<std::mutex> l(mutex_);require(live_&&!busy_,"UID storage busy/cancelled");busy_=true;
}
void SnapshotBridge::release() noexcept {
  std::lock_guard<std::mutex> l(mutex_);busy_=false;pending_=0;
}
std::string SnapshotBridge::read(bool* absent,Store store,const std::string& archive) {
  acquire();try{auto bytes=read_owned(absent,Clock::now()+std::chrono::seconds(10),store,archive);release();return bytes;}
  catch(...){release();throw;}
}
std::string SnapshotBridge::read_owned(bool* absent,Clock::time_point deadline,Store store,const std::string& archive) {
  const auto limit=store_limit(store);
  if(absent)*absent=false;
  std::string bytes,expected;uint64_t size=0;
  do {
    auto result=page(bytes.size(),bytes.empty(),deadline,store,archive);const auto* p=field(result.get(),"value");
    if(field(result.get(),"error")) {
      const auto* reason=field(result.get(),"reason_code");
      if(absent&&bytes.empty()&&cJSON_IsString(reason)&&std::string(reason->valuestring)=="FS_NOT_FOUND") {*absent=true;return {};}
      throw Refused("authoritative UID snapshot unavailable; no enrollment change");
    }
    require(cJSON_IsObject(p),"snapshot page required");
    require(integer(field(p,"offset"),limit)==bytes.size(),"snapshot offset differs");
    const auto total=integer(field(p,"size"),limit);
    if(bytes.empty()) {size=total;expected=str(field(p,"sha256"),64);
      require(expected.size()==64&&expected.find_first_not_of("0123456789abcdef")==std::string::npos,"snapshot digest invalid");}
    require(total==size,"snapshot size changed during read");
    const auto* data=field(p,"data_b64");require(cJSON_IsString(data)&&data->valuestring,"snapshot bytes required");
    auto decoded=aii::uid::decode_base64(data->valuestring,65536);
    // Zero bytes is a present, corrupt document, never typed absence. Recovery
    // must be able to preserve it; normal enrollment parsing still refuses it.
    require((!decoded.empty()||size==0)&&decoded.size()==integer(field(p,"bytes"),65536)&&decoded.size()<=size-bytes.size(),"snapshot page extent differs");
    bytes+=decoded;require(flag(field(p,"eof"))==(bytes.size()==size),"snapshot EOF differs");
  }while(bytes.size()<size);
  require(picosha2::hash256_hex_string(bytes)==expected,"snapshot digest mismatch");
  auto check=page(size,true,deadline,store,archive);const auto* p=field(check.get(),"value");
  const auto* data=field(p,"data_b64");
  require(integer(field(p,"offset"),limit)==size&&integer(field(p,"size"),limit)==size&&
    integer(field(p,"bytes"),65536)==0&&flag(field(p,"eof"))&&cJSON_IsString(data)&&data->valuestring&&!*data->valuestring&&
    str(field(p,"sha256"),64)==expected,"snapshot changed at readback");
  return bytes;
}
Json SnapshotBridge::publish(const std::string& candidate,const std::string& expected,bool absent,const std::string& upload,Store store,const std::string& archive) {
  const auto limit=store_limit(store);
  require(!candidate.empty()&&candidate.size()<=limit&&upload.size()==64&&
    upload.find_first_not_of("0123456789abcdef")==std::string::npos,"bounded enrollment publication required");
  require((absent&&expected.empty())||(!absent&&expected.size()==64&&expected.find_first_not_of("0123456789abcdef")==std::string::npos),"explicit base generation required");
  if(store==Store::Recovery)require(picosha2::hash256_hex_string(candidate)==archive&&
      (absent||expected==archive),"recovery archive is immutable and content addressed");
  acquire();bool publication_sent=false;
  try {
    const auto deadline=Clock::now()+std::chrono::seconds(30);
    for(size_t offset=0;offset<candidate.size();offset+=65536) {
      const auto n=std::min<size_t>(65536,candidate.size()-offset);
      auto q=object();put(q,"action",string("stage"));put(q,"upload",string(upload));
      put(q,"append",boolean(offset!=0));put(q,"data_b64",string(aii::uid::encode_base64(std::string_view(candidate).substr(offset,n))));
      auto receipt=exchange(std::move(q),deadline,store,archive);require(!field(receipt.get(),"error"),"enrollment upload refused; no publication requested");
      const auto* v=field(receipt.get(),"value");
      require(integer(field(v,"bytes"),65536)==n&&integer(field(v,"size"),limit)==offset+n,"enrollment upload extent differs; no publication requested");
    }
    const auto digest=picosha2::hash256_hex_string(candidate);
    auto q=object();put(q,"action",string("publish"));put(q,"upload",string(upload));put(q,"sha256",string(digest));
    if(absent)put(q,"expected_absent",boolean(true));else put(q,"expected_sha256",string(expected));
    publication_sent=true;auto receipt=exchange(std::move(q),deadline,store,archive);
    if(field(receipt.get(),"error")) {
      const auto* code=field(receipt.get(),"reason_code");
      if(cJSON_IsString(code)&&(std::string(code->valuestring)=="FS_GENERATION_MISMATCH"||std::string(code->valuestring)=="FS_DIGEST_MISMATCH")) {
        publication_sent=false;throw Refused("enrollment generation/digest conflict; not published, obtain a fresh confirmation");
      }
      throw Refused("enrollment publication unavailable");
    }
    auto* v=field(receipt.get(),"value");
    require(integer(field(v,"size"),limit)==candidate.size()&&str(field(v,"sha256"),64)==digest&&
      flag(field(v,"replaced"))==!absent,"enrollment publication receipt differs");
    const bool durable=flag(field(v,"durable"));const auto durability=str(field(v,"durability"),32);
    require((durable&&(durability=="synced"||durability=="file-synced"))||(!durable&&durability=="unknown"),"contradictory enrollment durability");
    require(read_owned(nullptr,deadline,store,archive)==candidate,"published enrollment readback differs");
    auto result=clone(v);put(result,"readback_verified",boolean(true));
    release();return result;
  }catch(const std::exception& e){release();if(publication_sent)throw Refused("enrollment publication/readback unresolved; do not assume unchanged or retry automatically");throw;}
  catch(...){release();throw;}
}
aii_voice_result SnapshotBridge::callback(void* p,char* bytes,size_t capacity,size_t* written) noexcept {
  try {
    if(!p||!bytes||!written)return AII_VOICE_INVALID;
    *written=0;auto raw=static_cast<SnapshotBridge*>(p)->read();
    if(raw.size()>capacity)return AII_VOICE_CAPACITY;
    std::memcpy(bytes,raw.data(),raw.size());*written=raw.size();return AII_VOICE_OK;
  }catch(...){return AII_VOICE_FAILED;}
}
}
