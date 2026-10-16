#include "snapshot_bridge.h"
#include "../../native_uid/snapshot.h"
#include "../vendor/picosha2/picosha2.h"
#include <chrono>
#include <atomic>
#include <future>
#include <iostream>
#include <mutex>
#include <thread>
#include <vector>
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
// A WRITE WAITS A WRITE'S TIME, A READ A READ'S. The host answers a durable
// write later than a read: a file synced, a rename, a directory synced, a
// record synced. The bridge gave both two seconds, and its carrier gave the
// host 1.5 s for either; on a slow disk a publication could not fit and the
// speaker was reported unavailable. With a table that gives a read 300 ms
// and a write 1.5 s: a publication whose stage and publish each answer after
// 700 ms is taken and read back; a page that answers after 700 ms is late.
// And a table that does not hold is refused before it is used.
static void a_write_waits_a_writes_time() {
  WorkerLimits limits;
  limits.exchange_read=300ms;limits.exchange_write=1500ms;limits.opening=1500ms;limits.whole_read=5000ms;limits.whole_publication=9000ms;
  const std::string candidate(1000,'c'),upload(64,'a');
  std::mutex m;std::string staged,current="prior";std::atomic<int> read_delay{0},write_delay{700};
  std::vector<std::thread> answers; // each answer is given later, off the caller, as a host's is
  SnapshotBridge b;b.limits(limits);
  // Joined before the bridge goes, on every way out: a check that fails says
  // its own words and is not lost to a thread still running.
  struct Join {std::vector<std::thread>& t;~Join(){for(auto& x:t)if(x.joinable())x.join();}} join{answers};
  b.sender([&](Json j){
    auto query=std::make_shared<Json>(std::move(j));
    answers.emplace_back([&,query]{
      const auto* q=field(query->get(),"snapshot_request");const auto* action=field(q,"action");
      std::this_thread::sleep_for(std::chrono::milliseconds(action?write_delay.load():read_delay.load()));
      Json r=object();
      {
        std::lock_guard<std::mutex> lock(m);
        if(!action)r=response(q,current);
        else {
          put(r,"id",clone(field(q,"id")));put(r,"session_id",clone(field(q,"session_id")));auto v=object();
          if(str(action)=="stage") {
            const auto chunk=aii::uid::decode_base64(str(field(q,"data_b64"),100000),65536);
            if(!flag(field(q,"append")))staged.clear();staged+=chunk;
            put(v,"bytes",number(chunk.size()));put(v,"size",number(staged.size()));
          } else {
            put(v,"size",number(staged.size()));put(v,"sha256",clone(field(q,"sha256")));put(v,"replaced",boolean(true));
            put(v,"durable",boolean(true));put(v,"durability",string("synced"));current=staged;
          }
          put(r,"value",std::move(v));
        }
      }
      try{b.accept(r.get());}catch(const std::exception&){} // an answer to a wait already given up is not this test's
    });
  });
  b.begin("slow-disk");
  const auto began=std::chrono::steady_clock::now();
  auto receipt=b.publish(candidate,picosha2::hash256_hex_string(std::string("prior")),false,upload,Store::Enrollment);
  const auto took=std::chrono::steady_clock::now()-began;
  check(flag(field(receipt.get(),"readback_verified")),"a publication whose writes each took 700 ms was not taken");
  check(took>=1400ms,"the writes did not take the time the test gave them");
  {std::lock_guard<std::mutex> lock(m);check(current==candidate,"the slow publication did not become the file");}
  // A read held to the same 700 ms is past a read's 300 ms: late, said so, and nothing invented.
  read_delay=700;
  try{(void)b.read(nullptr,Store::Enrollment);throw 42;}
  catch(const std::exception&){}catch(...){throw std::runtime_error("a page 400 ms past a read's time was waited for");}
  for(auto& t:answers)t.join();
  answers.clear();
  // A table that does not nest is refused, and the bridge keeps the one it had.
  WorkerLimits bad=limits;bad.exchange_write=100ms;
  try{b.limits(bad);throw 42;}catch(const std::exception&){}catch(...){throw std::runtime_error("a table that does not nest was taken");}
  check(b.limits().exchange_write==1500ms,"a refused table replaced the one in force");
}
// LATE IS SAID AS LATE, AND ONLY LATE. The carrier marks a query the host did
// not answer inside its time (HOST_STORAGE_NO_ANSWER); the bridge raises that
// as StorageLate, the model's reader gets BUSY and not FAILED, a stage that
// was late says nothing was published, a publish that was late says its
// outcome is unresolved, and a refusal with any other reason stays what it
// was. And storage says what it is doing: busy while an operation is in
// flight, one more ended when it is over.
static void late_storage_is_said_as_late() {
  const std::string candidate(1000,'c'),upload(64,'a'),prior="prior";
  std::string current=prior; // what a read gives: the file as the host has it
  std::string late; // "", "read", "stage", "publish", or "refused" (an error with no reason)
  bool busy_seen=false;
  SnapshotBridge b;
  b.sender([&](Json j){
    busy_seen=b.activity().busy;
    const auto* q=field(j.get(),"snapshot_request");const auto* action=field(q,"action");
    const std::string kind=action?str(action):"read";
    Json r=object();put(r,"id",clone(field(q,"id")));put(r,"session_id",clone(field(q,"session_id")));
    if(late==kind||late=="refused") {
      put(r,"error",string("host UID snapshot unavailable or invalid"));
      if(late!="refused")put(r,"reason_code",string(kHostStorageNoAnswer));
    } else if(!action) r=response(q,current);
    else {
      auto v=object();
      if(kind=="stage"){put(v,"bytes",number(candidate.size()));put(v,"size",number(candidate.size()));}
      else {put(v,"size",number(candidate.size()));put(v,"sha256",clone(field(q,"sha256")));put(v,"replaced",boolean(true));
        put(v,"durable",boolean(true));put(v,"durability",string("synced"));current=candidate;}
      put(r,"value",std::move(v));
    }
    b.accept(r.get());
  });
  b.begin("late");
  const auto before=b.activity();
  check(!before.busy,"storage is busy with nothing asked of it");
  check(b.read(nullptr,Store::Enrollment)==prior&&busy_seen,"a read in flight was not seen as storage at work");
  check(!b.activity().busy&&b.activity().completed==before.completed+1,"a read that ended was not counted as ended, once");

  const auto raised=[&](const std::function<void()>& f)->std::string {
    try{f();}catch(const StorageLate& e){return std::string("late: ")+e.what();}
    catch(const std::exception& e){return std::string("other: ")+e.what();}
    return "nothing";
  };
  char output[4096]{};size_t written=99;
  late="read";
  check(raised([&]{(void)b.read(nullptr,Store::Enrollment);}).rfind("late: ",0)==0,"a read the host did not answer in time was not raised as late");
  check(SnapshotBridge::callback(&b,output,sizeof output,&written)==AII_VOICE_BUSY&&written==0,"the model's reader was not told the storage was late");
  late="refused";
  check(raised([&]{(void)b.read(nullptr,Store::Enrollment);}).rfind("other: ",0)==0,"a refusal with no reason was raised as late");
  check(SnapshotBridge::callback(&b,output,sizeof output,&written)==AII_VOICE_FAILED&&written==0,"a refused read reached the model's reader as late");

  const auto publish=[&]{(void)b.publish(candidate,picosha2::hash256_hex_string(prior),false,upload,Store::Enrollment);};
  late="stage";
  auto said=raised(publish);
  check(said.rfind("late: ",0)==0&&said.find("no publication requested")!=std::string::npos&&said.find("unresolved")==std::string::npos,
        "a stage that was late did not say that nothing was published");
  late="publish";
  said=raised(publish);
  check(said.rfind("late: ",0)==0&&said.find("unresolved")!=std::string::npos,"a publish that was late did not say its outcome is unresolved");
  late="refused";
  said=raised(publish);
  check(said.rfind("other: ",0)==0,"a refused upload was raised as late");
  late.clear();
  const auto ended=b.activity().completed;
  publish();
  check(!b.activity().busy&&b.activity().completed==ended+1,"a publication that ended was not counted as ended, once");
}
int main(){try{
  a_write_waits_a_writes_time();
  late_storage_is_said_as_late();
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
