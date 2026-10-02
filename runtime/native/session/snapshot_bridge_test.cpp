#include "snapshot_bridge.h"
#include "../../native_uid/snapshot.h"
#include "../vendor/picosha2/picosha2.h"
#include <chrono>
#include <atomic>
#include <future>
#include <iostream>
using namespace aii::voice;
using namespace aii::voice::wire;
using namespace std::chrono_literals;
static void check(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
static Json response(const cJSON* q,const std::string& bytes,bool change=false) {
  auto j=object(),v=object();put(j,"id",clone(field(q,"id")));put(j,"session_id",clone(field(q,"session_id")));
  auto offset=integer(field(q,"offset"));auto n=std::min<size_t>(65536,bytes.size()-offset);
  put(v,"offset",number(offset));put(v,"size",number(bytes.size()));put(v,"bytes",number(n));
  put(v,"eof",boolean(offset+n==bytes.size()));put(v,"data_b64",string(aii::uid::encode_base64(std::string_view(bytes).substr(offset,n))));
  if(flag(field(q,"digest")))put(v,"sha256",string(change?std::string(64,'a'):picosha2::hash256_hex_string(bytes)));
  put(j,"value",std::move(v));return j;
}
using Store=SnapshotBridge::Store;
static void resource(const cJSON* q,Store store) {
  const auto* r=field(q,"resource");
  check(store==Store::Enrollment?!r:(r&&str(r)=="captures"),"cross-resource snapshot request");
}
static void readback_and_failure(Store store) {
  const std::string bytes(store==Store::Enrollment?100001:60001,'b');SnapshotBridge b;unsigned pages=0;bool change=false,failed=false;
  b.sender([&](Json j){auto* q=field(j.get(),"snapshot_request");resource(q,store);++pages;auto r=response(q,bytes,change&&integer(field(q,"offset"))==bytes.size());
    if(failed){r=object();put(r,"id",clone(field(q,"id")));put(r,"session_id",clone(field(q,"session_id")));put(r,"error",string("unavailable"));}
    b.accept(r.get());});
  b.begin("s");check(b.read(nullptr,store)==bytes&&pages==(store==Store::Enrollment?3:2),"paged snapshot/readback differs");
  change=true;try{(void)b.read(nullptr,store);throw 42;}catch(const std::exception&){}catch(...){throw std::runtime_error("changed readback accepted");}
  change=false;failed=true;try{(void)b.read(nullptr,store);throw 42;}catch(const std::exception&){}catch(...){throw std::runtime_error("failed read became empty enrollments");}
}
static void cancelled_late_and_foreign(Store store) {
  SnapshotBridge b;std::promise<Json> issued;b.sender([&](Json j){resource(field(j.get(),"snapshot_request"),store);issued.set_value(std::move(j));});b.begin("old");
  auto read=std::async(std::launch::async,[&]{try{b.read(nullptr,store);return false;}catch(const std::exception&){return true;}});
  auto future=issued.get_future();check(future.wait_for(1s)==std::future_status::ready,"read not issued");auto q=future.get();
  b.cancel();check(read.wait_for(100ms)==std::future_status::ready&&read.get(),"cancel waited behind host read");
  auto late=response(field(q.get(),"snapshot_request"),"old");
  // Switch resource as well as session: a stale pending-capture reply cannot
  // configure enrollment, nor can a stale enrollment reply replace captures.
  const auto next=store==Store::Enrollment?Store::PendingCaptures:Store::Enrollment;
  b.sender([&](Json j){resource(field(j.get(),"snapshot_request"),next);b.accept(late.get());auto r=response(field(j.get(),"snapshot_request"),"fresh");b.accept(r.get());});
  b.begin("new");check(b.read(nullptr,next)=="fresh","late old read configured next session");
  auto wrong=object();put(wrong,"id",number(1));put(wrong,"session_id",string("other"));
  try{b.accept(wrong.get());throw 42;}catch(const std::exception&){}catch(...){throw std::runtime_error("foreign snapshot accepted");}
}
static void publication(Store store) {
  const std::string candidate(store==Store::Enrollment?150001:60001,'b'),upload(64,'a');
  std::string current="prior",staged;bool missing=false,denied=false,conflict=false,durable=true,badread=false;
  unsigned publications=0;SnapshotBridge b;
  b.sender([&](Json j){const auto* q=field(j.get(),"snapshot_request");resource(q,store);auto r=object();
    put(r,"id",clone(field(q,"id")));put(r,"session_id",clone(field(q,"session_id")));
    const auto* action=field(q,"action");
    if(!action) {
      if(missing||denied){put(r,"error",string("unavailable"));put(r,"reason_code",string(denied?"FS_IO_FAILED":"FS_NOT_FOUND"));}
      else r=response(q,current,badread);
    }else if(str(action)=="stage") {
      const auto chunk=aii::uid::decode_base64(str(field(q,"data_b64"),100000),65536);
      if(!flag(field(q,"append")))staged.clear();staged+=chunk;
      auto v=object();put(v,"bytes",number(chunk.size()));put(v,"size",number(staged.size()));put(r,"value",std::move(v));
    }else {
      ++publications;
      if(conflict){put(r,"error",string("conflict"));put(r,"reason_code",string("FS_GENERATION_MISMATCH"));}
      else {check(staged==candidate,"partial candidate published");check(str(field(q,"sha256"),64)==picosha2::hash256_hex_string(staged),"wrong staged digest");
        if(missing)check(flag(field(q,"expected_absent")),"first file did not require absence");
        else check(str(field(q,"expected_sha256"),64)==picosha2::hash256_hex_string(current),"old generation not bound");
        auto v=object();put(v,"size",number(staged.size()));put(v,"sha256",clone(field(q,"sha256")));put(v,"replaced",boolean(!missing));
        put(v,"durable",boolean(durable));put(v,"durability",string(durable?"synced":"unknown"));put(r,"value",std::move(v));current=staged;missing=false;}
    }
    b.accept(r.get());
  });
  b.begin("s");bool absent=false;check(b.read(&absent,store)=="prior"&&!absent,"present read became absent");
  auto receipt=b.publish(candidate,picosha2::hash256_hex_string(current),false,upload,store);
  check(flag(field(receipt.get(),"readback_verified"))&&current==candidate&&publications==1,"publication/readback failed");
  conflict=true;try{b.publish(candidate,picosha2::hash256_hex_string(current),false,upload,store);throw 42;}catch(const std::exception&){}catch(...){throw std::runtime_error("CAS conflict accepted");}
  check(publications==2,"CAS automatically retried");conflict=false;missing=true;
  check(b.read(&absent,store).empty()&&absent,"typed first-file absence lost");
  if(store==Store::Enrollment) {
    char output[64]{};size_t written=99;
    check(SnapshotBridge::callback(&b,output,sizeof output,&written)==AII_VOICE_AGAIN&&written==0,
      "model reader did not preserve typed first-file absence");
  }
  try{b.read(nullptr,store);throw 42;}catch(const std::exception&){}catch(...){throw std::runtime_error("ordinary UID read treats missing as empty");}
  durable=false;receipt=b.publish(candidate,"",true,upload,store);
  check(!flag(field(receipt.get(),"durable"))&&flag(field(receipt.get(),"readback_verified")),"unsynced publication pretended durable");
  denied=true;try{b.read(&absent,store);throw 42;}catch(const std::exception&){}catch(...){throw std::runtime_error("IO failure became absent");}
  check(!absent,"unavailable set absence flag");denied=false;badread=true;
  if(store==Store::Enrollment) {
    char output[64]{};size_t written=99;
    denied=true;
    check(SnapshotBridge::callback(&b,output,sizeof output,&written)==AII_VOICE_FAILED&&written==0,
      "failed enrollment read masqueraded as typed absence");
    denied=false;
  }
  try{b.publish(candidate,picosha2::hash256_hex_string(current),false,upload,store);throw 42;}catch(const std::exception& e){check(std::string(e.what()).find("unresolved")!=std::string::npos,"published readback failure claimed unchanged");}catch(...){throw std::runtime_error("bad readback accepted");}
  if(store==Store::PendingCaptures){
    const auto before=publications;
    try{b.publish(std::string(65537,'b'),"",true,upload,store);throw 42;}catch(const std::exception&){}catch(...){throw std::runtime_error("oversize capture store published");}
    check(publications==before,"capture bound checked after publication");
  }
}
static void concurrent_recording_and_uid(bool recording_first,bool fail_owner=false) {
  SnapshotBridge bridge;std::promise<Json> held;
  std::atomic<bool> first{true};std::mutex host_mutex;
  std::string staged,waveform;unsigned publications=0;
  const std::string wave(70000,'w'),id(64,'c');
  auto service=[&](Json request) {
    std::lock_guard<std::mutex> lock(host_mutex);
    auto* q=field(request.get(),"snapshot_request");
    auto reply=object();put(reply,"id",clone(field(q,"id")));put(reply,"session_id",clone(field(q,"session_id")));
    if(fail_owner){fail_owner=false;put(reply,"error",string("fixture host read failed"));}
    else if(auto* action=field(q,"action")) {
      auto value=object();
      if(str(action)=="stage") {
        auto chunk=aii::uid::decode_base64(str(field(q,"data_b64"),100000),65536);
        if(!flag(field(q,"append")))staged.clear();staged+=chunk;
        put(value,"bytes",number(chunk.size()));put(value,"size",number(staged.size()));
      }else {
        check(str(action)=="publish"&&staged==wave,"recording write corrupted/interleaved");
        ++publications;waveform=staged;
        put(value,"size",number(waveform.size()));put(value,"sha256",string(picosha2::hash256_hex_string(waveform)));
        put(value,"replaced",boolean(false));put(value,"durable",boolean(true));put(value,"durability",string("synced"));
      }
      put(reply,"value",std::move(value));
    }else reply=response(q,field(q,"resource")?waveform:"profile");
    bridge.accept(reply.get());
  };
  bridge.sender([&](Json q){if(first.exchange(false))held.set_value(std::move(q));else service(std::move(q));});
  bridge.begin("same-session");
  auto run=[&](bool record){try {
    if(record) {auto saved=bridge.publish(wave,"",true,id,Store::Waveform,id);check(flag(field(saved.get(),"readback_verified")),"save not verified");}
    else check(bridge.read()=="profile","UID read changed");
    return true;
  }catch(const std::exception&){return false;}};
  const bool owner_should_fail=fail_owner;
  auto owner=std::async(std::launch::async,[&]{return run(recording_first);});
  auto query=held.get_future();check(query.wait_for(1s)==std::future_status::ready,"owner never requested storage");
  std::promise<void> entered;
  auto waiting=std::async(std::launch::async,[&]{entered.set_value();return run(!recording_first);});
  entered.get_future().wait();
  check(waiting.wait_for(20ms)==std::future_status::timeout,"storage contention refused instead of waiting");
  service(query.get());
  check(owner.get()!=owner_should_fail&&waiting.get(),"storage contention lost recording or UID");
  check(publications==(recording_first&&owner_should_fail?0:1),"recording publication repeated or lost");
}
static void cancel_storage_waiters() {
  SnapshotBridge bridge;std::promise<void> issued;bridge.sender([&](Json){issued.set_value();});bridge.begin("same");
  auto read=[&]{try{bridge.read();return false;}catch(const std::exception&){return true;}};
  auto owner=std::async(std::launch::async,read);issued.get_future().wait();
  std::promise<void> entered;
  auto waiting=std::async(std::launch::async,[&]{entered.set_value();return read();});entered.get_future().wait();
  check(waiting.wait_for(20ms)==std::future_status::timeout,"contending reader refused");
  bridge.cancel();
  check(owner.wait_for(100ms)==std::future_status::ready&&owner.get(),"owner ignored cancellation");
  // Reopening the same logical name must not revive a waiter from its prior epoch.
  bridge.begin("same");
  check(waiting.wait_for(100ms)==std::future_status::ready&&waiting.get(),"old waiter entered successor");
  bridge.sender([&](Json q){auto r=response(field(q.get(),"snapshot_request"),"recovered");bridge.accept(r.get());});
  check(bridge.read()=="recovered","cancellation poisoned successor");
}
int main(){try{
  concurrent_recording_and_uid(false);concurrent_recording_and_uid(true);
  concurrent_recording_and_uid(false,true);concurrent_recording_and_uid(true,true);
  cancel_storage_waiters();
  auto literal=parse(R"({"label":"literal\\u0000"})");check(str(field(literal.get(),"label"))==R"(literal\u0000)","literal backslash rejected");
  try{parse(R"({"label":"truncated\u0000hidden"})");throw 42;}catch(const std::exception&){}catch(...){throw std::runtime_error("embedded NUL accepted");}
  for(auto store:{Store::Enrollment,Store::PendingCaptures}){
    readback_and_failure(store);cancelled_late_and_foreign(store);publication(store);
  }
  std::cout<<"two fixed UID stores: paged read, CAS/readback, durability, unavailable, cancellation and session/resource fencing PASS\n";}
catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
