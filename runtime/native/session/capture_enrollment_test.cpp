#include "capture_enrollment.h"
#include "uid_recovery.h"
#include "speaker_registry_store.h"
#include "attribution.h"
#include "../vendor/picosha2/picosha2.h"
#include <iostream>
#include <optional>
#include <filesystem>
#include <fstream>
#include <cmath>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <thread>

using namespace aii::voice;
using namespace aii::voice::wire;
using namespace aii::uid;
namespace {
void check(bool value,const char* why){if(!value)throw std::runtime_error(why);}
std::string hash(const std::string& x){return picosha2::hash256_hex_string(x);}
template<class F> void refused(F f,const char* needle){
  try{f();}catch(const std::exception& error){
    if(std::string(error.what()).find(needle)==std::string::npos)throw std::runtime_error(std::string("wrong refusal: ")+error.what());
    return;
  }throw std::runtime_error(std::string("not refused: ")+needle);
}
template<class F> std::string refusal(F f){
  try{f();}catch(const std::exception& error){return error.what();}
  throw std::runtime_error("not refused");
}
void contains(const std::string& text,const std::string& needle){
  if(text.find(needle)==std::string::npos)throw std::runtime_error("wrong refusal: "+text+" (expected: "+needle+")");
}
PolicyDocument policy(unsigned floor=1,char calibration='c'){return read_policy("{\"calibration_sha256\":\""+std::string(64,calibration)+
  "\",\"embedding_binding\":\""+std::string(64,'b')+"\",\"minimum_enrollment_samples\":"+std::to_string(floor)+
  ",\"minimum_margin\":0.105,\"threshold\":0.56}");}
PendingCapture evidence(unsigned number=0){
  Vector v(256);v.at(number)=.6;v.at(number+1)=.8;
  return make_pending_capture(hash("request"+std::to_string(number)),1,160000,std::string(64,'b'),
      {hash("audio"+std::to_string(number)),v});
}
// Test host, not another production store: implements the existing bridge's
// two fixed files, staging, generation comparison, receipts and readback.
// A fault names one resource; "recovery" names every recovery archive.
struct Host {
  std::optional<std::string> profile,pending;
  std::map<std::string,std::optional<std::string>> archives;
  std::map<std::string,std::string> staged;
  std::vector<std::string> publications;
  std::string fail_publish,unsynced,lost_receipt,bad_read,denied_read,corrupt_readback;
  std::filesystem::path disk;
  unsigned reads=0;
  std::mutex gate_mutex;
  std::condition_variable gate_changed;
  bool hold_registry_read=false,registry_read_entered=false;
  SnapshotBridge bridge;
  explicit Host(std::filesystem::path directory={}):disk(std::move(directory)){
    if(!disk.empty())for(const auto* name:{"enrollment","captures"}){
      const auto file=disk/(std::string(name)+".json");
      if(std::filesystem::exists(file)){
        std::ifstream in(file,std::ios::binary);check(bool(in),"test-broker read refused");
        std::string bytes((std::istreambuf_iterator<char>(in)),{});check(!in.bad(),"test-broker read failed");
        (std::string(name)=="enrollment"?profile:pending)=bytes;
      }
    }
    bridge.sender([this](Json request){answer(std::move(request));});bridge.begin("management-no-microphone");
  }
  void answer(Json request){
    auto* q=field(request.get(),"snapshot_request");
    const std::string key=field(q,"resource")?str(field(q,"resource")):"enrollment";
    if(key=="speaker_registry" && !field(q,"action")) {
      std::unique_lock<std::mutex> lock(gate_mutex);
      if(hold_registry_read) {
        registry_read_entered=true;gate_changed.notify_all();
        gate_changed.wait(lock,[&]{return !hold_registry_read;});
      }
    }
    check(key=="captures"||key=="enrollment"||key=="speaker_registry"||key.rfind("recovery:",0)==0,"arbitrary private resource");
    const auto fault_key=key.rfind("recovery:",0)==0?"recovery":key;
    const auto faulted=[&](const std::string& fault){return fault==key||fault==fault_key;};
    auto& current=key=="captures"?pending:key=="enrollment"?profile:archives[key];
    auto reply=object();put(reply,"id",clone(field(q,"id")));put(reply,"session_id",clone(field(q,"session_id")));
    auto error=[&](const char* why,const char* code){put(reply,"error",string(why));put(reply,"reason_code",string(code));};
    const auto* action=field(q,"action");
    if(!action){
      ++reads;
      if(denied_read==key)error("read denied","FS_IO_FAILED");
      else if(!current)error("absent","FS_NOT_FOUND");
      else {
        const auto offset=integer(field(q,"offset"));check(offset<=current->size(),"out of bound read");
        const auto n=std::min<size_t>(65536,current->size()-offset);auto v=object();
        put(v,"offset",number(offset));put(v,"size",number(current->size()));put(v,"bytes",number(n));
        put(v,"eof",boolean(offset+n==current->size()));
        put(v,"data_b64",string(encode_base64(std::string_view(*current).substr(offset,n))));
        if(flag(field(q,"digest")))put(v,"sha256",string(bad_read==key?std::string(64,'0'):hash(*current)));
        put(reply,"value",std::move(v));
      }
    }else if(str(action)=="stage"){
      const auto chunk=decode_base64(str(field(q,"data_b64"),100000),65536);
      if(!flag(field(q,"append")))staged[key].clear();staged[key]+=chunk;
      auto v=object();put(v,"bytes",number(chunk.size()));put(v,"size",number(staged[key].size()));put(reply,"value",std::move(v));
    }else{
      check(str(action)=="publish","unknown action");publications.push_back(key);
      if(faulted(fail_publish))error("generation changed","FS_GENERATION_MISMATCH");
      else {
        if(current)check(str(field(q,"expected_sha256"),64)==hash(*current),"profile/capture CAS base ignored");
        else check(flag(field(q,"expected_absent")),"absent file not guarded");
        check(str(field(q,"sha256"),64)==hash(staged.at(key)),"staged hash differs");
        const bool replaced=current.has_value();current=staged.at(key);
        if(!disk.empty()){
          // A restart/codec test broker only. Its simulated synced receipt is
          // NOT a qualification of real host fsync, containment or power loss.
          std::ofstream out(disk/(key+".json"),std::ios::binary|std::ios::trunc);
          out<<*current;out.close();check(bool(out),"test-broker persistence failed");
        }
        if(corrupt_readback==key)bad_read=key;
        if(lost_receipt==key)error("receipt unavailable after rename","FS_IO_FAILED");
        else {
          auto v=object();put(v,"size",number(current->size()));put(v,"sha256",string(hash(*current)));
          put(v,"replaced",boolean(replaced));put(v,"durable",boolean(!faulted(unsynced)));
          put(v,"durability",string(faulted(unsynced)?"unknown":"synced"));put(reply,"value",std::move(v));
        }
      }
    }
    bridge.accept(reply.get());
  }
};
void retained(Host& host,const PolicyDocument& p,const PendingCapture& capture){
  CaptureEnrollment flow(host.bridge,p);
  check(flag(field(flow.retain(capture,hash("retain")).get(),"durable")),"capture not durably retained");
  check(!host.profile&&flow.list().size()==1,"retention silently enrolled a person");
  host.publications.clear();
}
void complete_and_reopen(){
  Host host;const auto p=policy();const auto c=evidence();retained(host,p,c);
  // Destroyed composition owner, closed microphone, no inference handle.
  host.bridge.cancel();host.bridge.begin("new-activation-after-capture");
  CaptureEnrollment flow(host.bridge,p);check(flow.list()[0].id==c.id,"capture expired with session");
  auto r=flow.confirm(c.id,"sam","Sam",hash("confirm"));
  check(r.enrollment_durable&&r.capture_retirement_durable&&!r.reconciled,"complete confirmation not durable");
  check(host.publications==std::vector<std::string>{"enrollment","captures"},"capture retired before profile publication");
  const auto enrolled=read_snapshot(*host.profile,p);
  check(enrolled.revision==1&&enrolled.speakers.size()==1&&enrolled.speakers[0].samples.size()==1,"single capture did not enroll exactly once");
  check(flow.list().empty(),"committed capture still pending");
  const auto before=*host.profile;refused([&]{flow.confirm(c.id,"sam","Sam",hash("repeat"));},"selected pending capture unavailable");
  check(*host.profile==before,"missing capture replay changed enrollment");
}
void interrupted_publication(){
  for(const std::string fault:{"failed","unsynced","lost","bad-readback"}){
    Host host;const auto p=policy();const auto c=evidence();retained(host,p,c);const auto original=*host.pending;
    CaptureEnrollment flow(host.bridge,p);
    if(fault=="failed")host.fail_publish="enrollment";
    if(fault=="unsynced")host.unsynced="enrollment";
    if(fault=="lost")host.lost_receipt="enrollment";
    if(fault=="bad-readback")host.corrupt_readback="enrollment";
    if(fault=="unsynced"){
      auto r=flow.confirm(c.id,"sam","Sam",hash("first"));
      check(!r.enrollment_durable&&!r.capture_retirement_durable&&!r.cleanup_detail.empty(),"unsynced profile became completed enrollment");
    }else refused([&]{flow.confirm(c.id,"sam","Sam",hash("first"));},fault=="failed"?"not published":"publication/readback unresolved");
    check(host.pending&&*host.pending==original&&host.publications==std::vector<std::string>{"enrollment"},"failed/uncertain profile erased pending evidence");
    check(host.profile.has_value()==(fault!="failed"),"test did not exercise the published-but-uncertain path");
    const auto prior=host.profile;
    host.fail_publish.clear();host.unsynced.clear();host.lost_receipt.clear();host.bad_read.clear();host.corrupt_readback.clear();host.publications.clear();
    host.bridge.cancel();host.bridge.begin("restart-reconcile");CaptureEnrollment fresh(host.bridge,p);
    auto r=fresh.confirm(c.id,"sam","Sam",hash("explicit-new-confirmation"));
    check(r.enrollment_durable&&r.capture_retirement_durable&&r.reconciled==(fault!="failed"),"explicit reconciliation failed");
    if(prior)check(*prior==*host.profile,"reconciliation duplicated evidence or advanced profile revision");
    check(host.publications==std::vector<std::string>{"enrollment","captures"},"existing bytes bypassed durability re-attestation");
  }
}
void interrupted_cleanup(){
  for(const std::string fault:{"conflict","unsynced","lost"}){
    Host host;const auto p=policy();const auto c=evidence();retained(host,p,c);const auto original=*host.pending;
    if(fault=="conflict")host.fail_publish="captures";
    if(fault=="unsynced")host.unsynced="captures";
    if(fault=="lost")host.lost_receipt="captures";
    CaptureEnrollment flow(host.bridge,p);auto r=flow.confirm(c.id,"sam","Sam",hash("confirm"));
    check(r.enrollment_durable&&!r.capture_retirement_durable&&!r.cleanup_detail.empty(),"cleanup failure misreported committed enrollment");
    const auto profile=*host.profile;
    if(fault=="conflict")check(*host.pending==original,"cleanup conflict overwrote pending captures");
    // A crash may recover the prior unsynced pending file. Restoring it here is
    // explicit fault injection, not a claim to have caused a physical power loss.
    host.pending=original;host.fail_publish.clear();host.unsynced.clear();host.lost_receipt.clear();host.publications.clear();
    host.bridge.cancel();host.bridge.begin("cleanup-restart");CaptureEnrollment next(host.bridge,p);
    auto again=next.confirm(c.id,"sam","Sam",hash("new-act"));
    check(again.reconciled&&again.enrollment_durable&&again.capture_retirement_durable&&*host.profile==profile,
        "recovered pending capture duplicated/replaced profile");
  }
}
void refusals(){
  Host host;const auto p=policy();const auto c=evidence();retained(host,p,c);CaptureEnrollment flow(host.bridge,p);
  const auto before_reads=host.reads;
  refused([&]{flow.confirm(c.id,"sam","Sam","arbitrary");},"upload identity invalid");
  check(host.reads==before_reads,"invalid confirmation accessed evidence");
  host.denied_read="captures";refused([&]{flow.list();},"unavailable");host.denied_read.clear();
  host.denied_read="enrollment";refused([&]{flow.confirm(c.id,"sam","Sam",hash("x"));},"unavailable");host.denied_read.clear();
  check(host.publications.empty(),"read failure became missing profile");
  const auto blank=write_snapshot({p.policy,0,{}},p);
  host.profile=prepare_enrollment(blank,p,"other","Other",{c.recording},c.embedding_binding).snapshot;
  refused([&]{flow.confirm(c.id,"sam","Sam",hash("x"));},"conflicts with already enrolled");
  host.profile=prepare_enrollment(blank,p,"sam","Wrong label",{c.recording},c.embedding_binding).snapshot;
  refused([&]{flow.confirm(c.id,"sam","Sam",hash("x"));},"conflicts with already enrolled");
  auto changed=c.recording;changed.embedding=Vector(256);changed.embedding[9]=1;
  host.profile=prepare_enrollment(blank,p,"sam","Sam",{changed},c.embedding_binding).snapshot;
  refused([&]{flow.confirm(c.id,"sam","Sam",hash("x"));},"conflicts with already enrolled");
  check(host.publications.empty(),"conflicting existing identity/evidence was overwritten");
  refused([&]{CaptureEnrollment wrong(host.bridge,policy(3));},"verified guided-enrollment policy required");
  // No silent migration: a valid old-policy snapshot still refuses under the
  // guided policy until its explicit operator-confirmed transition is made.
  host.profile=write_snapshot({policy(3).policy,0,{}},policy(3));
  refused([&]{flow.confirm(c.id,"sam","Sam",hash("x"));},"policy");
  check(host.publications.empty(),"old profile policy silently replaced");
}
void process_step(const std::string& mode,const std::filesystem::path& directory){
  const auto p=policy();const auto c=evidence();
  if(mode=="retain"){
    check(!std::filesystem::exists(directory),"restart fixture directory already exists");
    std::filesystem::create_directories(directory);
  }
  check(std::filesystem::is_directory(directory),"restart fixture directory missing");
  Host host(directory);CaptureEnrollment flow(host.bridge,p);
  if(mode=="retain")retained(host,p,c);
  else if(mode=="confirm"){
    check(!host.profile&&flow.list().at(0).id==c.id,"fresh process did not recover selected evidence");
    const auto result=flow.confirm(c.id,"sam","Sam",hash("fresh-process-confirmation"));
    check(result.enrollment_durable&&result.capture_retirement_durable,"fresh-process confirmation failed");
  }else if(mode=="verify"){
    check(host.profile&&flow.list().empty(),"fresh process lost completed publication");
    const auto snapshot=read_snapshot(*host.profile,p);
    const auto known=identify(snapshot,p.policy,c.recording.embedding,c.embedding_binding);
    Vector unknown(256);unknown[20]=1;
    const auto other=identify(snapshot,p.policy,unknown,c.embedding_binding);
    check(snapshot.revision==1&&snapshot.speakers.size()==1&&snapshot.speakers[0].samples.size()==1&&
        known.speaker_id=="sam"&&other.speaker_id.empty(),"restarted profile changed known/unknown identity");
  }else throw std::invalid_argument("unknown process step");
  std::cout<<"fresh-process "<<mode<<" PASS; fixture broker, no host durability or acoustic claim\n";
}
Json corroborated(SpeakerRegistryStore& store,const aii_voice_capture& sample) {
  static uint64_t utterance=100;
  auto earlier=sample;
  const auto distinct=hash(std::string(sample.pcm_sha256)+std::to_string(utterance));
  std::snprintf(earlier.pcm_sha256,sizeof earlier.pcm_sha256,"%s",distinct.c_str());
  auto pending=store.observe(&earlier,1,++utterance);
  check(str(field(pending.get(),"reason"))=="speaker_profile_pending"&&!field(pending.get(),"speaker_uuid"),
    "fixture's first observation must not publish");
  return store.observe(&sample,1,++utterance);
}
void registry_management_does_not_discard_live_observation() {
  Host host;const auto p=policy();SpeakerRegistryStore store(host.bridge,p);
  {
    std::lock_guard<std::mutex> lock(host.gate_mutex);
    host.hold_registry_read=true;
  }
  std::exception_ptr management_error,observation_error;
  std::thread management([&]{try{(void)store.list();}catch(...){management_error=std::current_exception();}});
  bool read_entered=false;
  {
    std::unique_lock<std::mutex> lock(host.gate_mutex);
    read_entered=host.gate_changed.wait_for(lock,std::chrono::seconds(3),[&]{return host.registry_read_entered;});
  }
  std::atomic<bool> observation_started=false;
  aii_voice_capture sample{};sample.samples=64000;sample.embedding[0]=1;
  std::snprintf(sample.embedding_binding,sizeof sample.embedding_binding,"%s",p.policy.embedding_binding.c_str());
  std::snprintf(sample.pcm_sha256,sizeof sample.pcm_sha256,"%s",hash("during-management").c_str());
  std::string reason;
  std::thread speaker([&]{
    observation_started=true;
    try {auto result=store.observe(&sample,1,1);reason=str(field(result.get(),"reason"));}
    catch(...){observation_error=std::current_exception();}
  });
  while(!observation_started.load())std::this_thread::yield();
  std::this_thread::sleep_for(std::chrono::milliseconds(30));
  {
    std::lock_guard<std::mutex> lock(host.gate_mutex);
    host.hold_registry_read=false;
  }
  host.gate_changed.notify_all();
  management.join();speaker.join();
  check(read_entered,"management did not hold the registry read");
  if(management_error)std::rethrow_exception(management_error);
  if(observation_error)std::rethrow_exception(observation_error);
  check(reason=="speaker_profile_pending","management collision discarded the live observation");
}
void registry_contract(){
  Host host;const auto p=policy();SpeakerRegistryStore store(host.bridge,p);
  auto empty=store.list();check(str(field(empty.get(),"registry_revision"))=="0","new registry not empty");
  auto unresolved=store.observe(nullptr);
  check(!field(unresolved.get(),"speaker_uuid")&&host.publications.empty(),"no-evidence observation wrote an empty registry");
  aii_voice_capture sample{};sample.samples=64000;
  std::snprintf(sample.embedding_binding,sizeof sample.embedding_binding,"%s",p.policy.embedding_binding.c_str());
  std::snprintf(sample.pcm_sha256,sizeof sample.pcm_sha256,"%s",hash("clean-track").c_str());sample.embedding[0]=1;
  {
    Host h;SpeakerRegistryStore joint(h.bridge,p);
    const auto anonymous=corroborated(joint,sample);
    const auto anonymous_id=str(field(anonymous.get(),"speaker_uuid"));
    const auto registry=read_registry(*h.archives.at("speaker_registry"),p);
    const auto& anonymous_samples=registry.profiles.speakers.at(0).samples;
    h.profile=write_snapshot({p.policy,1,{{"enrolled","Known speaker",anonymous_samples}}},p);
    const auto same=joint.observe(&sample,2,10);
    check(str(field(same.get(),"outcome"))=="known"&&
      str(field(same.get(),"speaker_id"))=="enrolled"&&
      str(field(same.get(),"speaker_uuid"))==anonymous_id,
      "exactly re-enrolled anonymous recordings remained competing speakers");
    h.profile=write_snapshot({p.policy,2,{{"enrolled","Known speaker",{anonymous_samples.at(0)}}}},p);
    const auto incomplete=joint.observe(&sample,2,11);
    check(str(field(incomplete.get(),"reason"))=="cross_gallery_ambiguous"&&
      !field(incomplete.get(),"speaker_uuid"),
      "partially shared recordings silently merged two galleries");
    Vector enrolled_vector(256);enrolled_vector[1]=1;
    h.profile=write_snapshot({p.policy,3,{{"other-enrolled","Known speaker",{{hash("enrolled"),enrolled_vector}}}}},p);
    auto guest=sample;std::snprintf(guest.pcm_sha256,sizeof guest.pcm_sha256,"%s",hash("guest").c_str());
    check(str(field(joint.observe(&guest,2,1).get(),"speaker_uuid"))==anonymous_id,
      "weaker enrolled candidate displaced stronger anonymous profile");
    auto known=guest;known.embedding[0]=0;known.embedding[1]=1;
    std::snprintf(known.pcm_sha256,sizeof known.pcm_sha256,"%s",hash("known").c_str());
    const auto enrolled_match=joint.observe(&known,2,2);
    check(str(field(enrolled_match.get(),"outcome"))=="known"&&
      str(field(enrolled_match.get(),"speaker_id"))=="other-enrolled"&&
      str(field(enrolled_match.get(),"speaker_uuid"))!=anonymous_id,"stronger enrolled profile lost to anonymous precedence");
    Vector overlapping(256);overlapping[0]=1;
    h.profile=write_snapshot({p.policy,2,{{"enrolled","Known speaker",{{hash("enrolled-new"),overlapping}}}}},p);
    const auto tied=joint.observe(&guest,2,3);
    check(!field(tied.get(),"speaker_uuid")&&str(field(tied.get(),"reason"))=="cross_gallery_ambiguous",
      "equal enrolled/anonymous evidence claimed a speaker");
    h.profile.reset();
    const auto first_track=joint.observe(&guest,2,4);
    const auto sibling=joint.observe(&guest,2,4);
    check(str(field(first_track.get(),"speaker_uuid"))==anonymous_id &&
      !field(sibling.get(),"speaker_uuid")&&
      str(field(sibling.get(),"reason"))=="same_speaker_on_multiple_tracks",
      "simultaneous tracks both claimed one speaker UUID");
  }
  {
    Host h;SpeakerRegistryStore guarded(h.bridge,p);
    const auto pending=guarded.observe(&sample,1,1);
    check(!field(pending.get(),"speaker_uuid")&&h.publications.empty()&&
      str(field(pending.get(),"reason"))=="speaker_profile_pending","one-shot unknown wrote durable state");
    auto next=sample;std::snprintf(next.pcm_sha256,sizeof next.pcm_sha256,"%s",hash("other-track").c_str());
    check(!field(guarded.observe(&next,1,1).get(),"speaker_uuid")&&h.publications.empty(),"same utterance confirmed itself");
    check(!field(guarded.observe(&next,1,2).get(),"speaker_uuid")&&h.publications.empty(),"replayed sibling confirmed itself");
    std::snprintf(next.pcm_sha256,sizeof next.pcm_sha256,"%s",hash("new-utterance").c_str());
    check(field(guarded.observe(&next,1,2).get(),"speaker_uuid")&&h.publications.size()==1,"corroborated new speaker was not published");
    const auto persisted=read_registry(*h.archives.at("speaker_registry"),p);
    check(persisted.profiles.speakers.size()==1&&persisted.profiles.speakers[0].samples.size()==2,
      "durable profile discarded the independent corroborating observation");
    const auto before=h.publications.size();
    auto near=sample;near.embedding[0]=.525;near.embedding[1]=std::sqrt(1-near.embedding[0]*near.embedding[0]);
    std::snprintf(near.pcm_sha256,sizeof near.pcm_sha256,"%s",hash("near-floor").c_str());
    const auto rejected=guarded.observe(&near,1,3);
    check(!field(rejected.get(),"speaker_uuid")&&field(rejected.get(),"match")&&h.publications.size()==before,
      "near-floor one-shot observation created a duplicate or lost diagnostics");
    SpeakerRegistryStore restarted_guard(h.bridge,p);
    std::snprintf(near.pcm_sha256,sizeof near.pcm_sha256,"%s",hash("after-restart").c_str());
    check(!field(restarted_guard.observe(&near,1,1).get(),"speaker_uuid")&&h.publications.size()==before,
      "transient proposal survived owner restart as corroboration");
  }
  auto first=corroborated(store,sample);const auto id=str(field(first.get(),"speaker_uuid"));
  check(str(field(first.get(),"continuity"))=="new_profile"&&host.publications.size()==1,"registry publication missing");
  auto listed=store.list();bool evidence_found=false;
  for(auto* row=field(listed.get(),"speakers")->child;row;row=row->next)if(str(field(row,"speaker_uuid"))==id) {
    const auto* evidence=field(row,"evidence_sha256");
    check(cJSON_IsArray(evidence)&&cJSON_GetArraySize(evidence)==2,
      "speaker profile evidence projection missing");
    bool first_digest=false;
    for(auto* digest=evidence->child;digest;digest=digest->next)
      first_digest |= str(digest)==hash("clean-track");
    check(first_digest&&!field(row,"embedding_f64le_b64"),"speaker evidence digest missing or embedding exposed");
    evidence_found=true;
  }
  check(evidence_found,"speaker evidence projection lost its UUID");
  auto same=store.observe(&sample);check(str(field(same.get(),"speaker_uuid"))==id&&host.publications.size()==1,"matched voice duplicated");
  const auto* match=field(same.get(),"match");
  check(str(field(match,"candidate_uuid"))==id&&str(field(match,"reason"))=="accepted"&&
    integer(field(match,"evidence_samples"))==64000&&integer(field(match,"candidate_count"))==1,
    "store dropped decision evidence");
  host.bridge.cancel();host.bridge.begin("later-session");SpeakerRegistryStore restarted(host.bridge,p);
  auto named=restarted.associate(1,id,"Chosen speaker","");
  check(str(field(named.get(),"registry_revision"))=="2","closed-session naming missing");
  same=restarted.observe(&sample);check(str(field(same.get(),"speaker_uuid"))==id&&str(field(same.get(),"display_label"))=="Chosen speaker","restart lost UUID/label");
  refused([&]{restarted.associate(1,id,"Stale","");},"stale");
  const auto writes=host.publications.size();
  for(unsigned i=0;i<300;++i) {
    auto provisional=restarted.observe(nullptr);
    check(!field(provisional.get(),"speaker_uuid")&&host.publications.size()==writes,
      "unresolved audio borrowed an identity or wrote durable buckets");
  }
  check(!host.profile&&!host.pending,"registry overwrote legacy stores");
  refused([&]{restarted.forget(1,id);},"stale");
  auto forgotten=restarted.forget(2,id);
  check(cJSON_GetArraySize(field(forgotten.get(),"speakers"))==0,"forget retained acoustic profile");
  auto reobserved=corroborated(restarted,sample);
  check(str(field(reobserved.get(),"speaker_uuid"))!=id&&str(field(reobserved.get(),"continuity"))=="new_profile",
      "forgotten UUID was revived");
  for(const std::string fault:{"denied","conflict","unsynced","readback"}) {
    Host broken;SpeakerRegistryStore owner(broken.bridge,p);
    if(fault=="denied")broken.denied_read="speaker_registry";
    if(fault=="conflict")broken.fail_publish="speaker_registry";
    if(fault=="unsynced")broken.unsynced="speaker_registry";
    if(fault=="readback")broken.bad_read="speaker_registry";
    refused([&]{corroborated(owner,sample);},fault=="denied"?"unavailable":fault=="conflict"?"not published":fault=="unsynced"?"durability":"unresolved");
  }
  for(const std::string fault:{"none","denied","conflict","unsynced","readback"}) {
    Host h;SpeakerRegistryStore owner(h.bridge,p);
    const auto target_result=corroborated(owner,sample);
    const auto target=str(field(target_result.get(),"speaker_uuid"));
    auto other=sample;other.embedding[0]=0;other.embedding[1]=1;
    std::snprintf(other.pcm_sha256,sizeof other.pcm_sha256,"%s",hash("second-clean-track").c_str());
    const auto source_result=corroborated(owner,other);
    const auto source=str(field(source_result.get(),"speaker_uuid"));
    if(fault=="denied")h.denied_read="speaker_registry";
    if(fault=="conflict")h.fail_publish="speaker_registry";
    if(fault=="unsynced")h.unsynced="speaker_registry";
    if(fault=="readback")h.corrupt_readback="speaker_registry";
    if(fault!="none") {
      refused([&]{owner.link(2,source,target);},fault=="denied"?"unavailable":fault=="conflict"?"not published":fault=="unsynced"?"durability":"unresolved");
      continue;
    }
    owner.link(2,source,target);
    h.bridge.cancel();h.bridge.begin("restart-linked-speakers");SpeakerRegistryStore fresh(h.bridge,p);
    auto observed=fresh.observe(&other);
    check(str(field(observed.get(),"speaker_uuid"))==target&&
      str(field(field(observed.get(),"match"),"candidate_uuid"))==source,"restart lost confirmed correction or acoustic provenance");
    const auto rows=fresh.list();bool found=false;
    for(auto* row=field(rows.get(),"speakers")->child;row;row=row->next)if(str(field(row,"speaker_uuid"))==source) {
      found=true;check(str(field(row,"canonical_uuid"))==target&&str(field(row,"link_revision"))=="3","correction invisible to consumer");
    }
    check(found,"source UUID disappeared after correction");
    fresh.link(3,source,source);observed=fresh.observe(&other);
    check(str(field(observed.get(),"speaker_uuid"))==source,"confirmed undo did not restore source UUID");
  }
}
void inherited_label_clear_contract() {
  const auto p=policy();Vector v(256);v[0]=1;
  for(const std::string fault:{"none","conflict","unsynced","readback"}) {
    Host host;host.profile=write_snapshot({p.policy,1,{{"legacy-id","Inherited name",{{hash("enrolled-origin"),v}}}}},p);
    const auto enrollment=*host.profile;
    aii_voice_capture sample{};sample.samples=64000;sample.embedding[0]=1;
    std::snprintf(sample.embedding_binding,sizeof sample.embedding_binding,"%s",p.policy.embedding_binding.c_str());
    std::snprintf(sample.pcm_sha256,sizeof sample.pcm_sha256,"%s",hash("fresh-speech").c_str());
    SpeakerRegistryStore store(host.bridge,p);
    const auto observed=store.observe(&sample,1,1);
    const auto id=str(field(observed.get(),"speaker_uuid"));
    check(str(field(observed.get(),"display_label"))=="Inherited name","fixture did not inherit enrollment label");
    const auto prior=*host.archives.at("speaker_registry");
    const auto before=host.publications.size();
    if(fault=="conflict")host.fail_publish="speaker_registry";
    if(fault=="unsynced")host.unsynced="speaker_registry";
    if(fault=="readback")host.corrupt_readback="speaker_registry";
    if(fault!="none") {
      refused([&]{store.associate(1,id,"","");},fault=="conflict"?"not published":fault=="unsynced"?"durability":"unresolved");
      check(*host.profile==enrollment,"failed clear changed enrollment");
      if(fault=="conflict")check(*host.archives.at("speaker_registry")==prior,"conflict changed registry");
      continue;
    }
    const auto cleared=store.associate(1,id,"","");
    check(str(field(cleared.get(),"registry_revision"))=="2"&&host.publications.size()==before+1,
      "first inherited-label clear did not publish revision 2");
    const auto bytes=*host.archives.at("speaker_registry");
    const auto registry=read_registry(bytes,p),old=read_registry(prior,p);
    const auto& bucket=registry.buckets.at(0);
    check(registry.revision==2&&bucket.uuid==id&&bucket.associations.size()==1&&
      bucket.associations.back().label.empty()&&bucket.associations.back().external_id.empty(),
      "inherited clear did not persist explicit empty metadata");
    check(*host.profile==enrollment&&write_snapshot(registry.profiles,p)==write_snapshot(old.profiles,p)&&
      bucket.enrollment->id==old.buckets.at(0).enrollment->id&&
      bucket.enrollment->evidence==old.buckets.at(0).enrollment->evidence,
      "metadata clear changed enrollment or acoustic evidence");
    host.bridge.cancel();host.bridge.begin("restart-cleared-enrollment");
    SpeakerRegistryStore fresh(host.bridge,p);
    const auto listing=fresh.list();const auto* row=field(listing.get(),"speakers")->child;
    check(str(field(row,"speaker_uuid"))==id&&str(field(row,"display_label"))=="unknown"&&
      flag(field(row,"matching_ready")),"reloaded listing lost clear or recognition readiness");
    const auto speech=fresh.observe(&sample,2,1);
    check(str(field(speech.get(),"speaker_uuid"))==id&&str(field(speech.get(),"display_label"))=="unknown",
      "reloaded observation inherited cleared enrollment label");
    const auto again=fresh.associate(2,id,"","");
    check(str(field(again.get(),"registry_revision"))=="2"&&host.publications.size()==before+1&&
      *host.archives.at("speaker_registry")==bytes,"repeated clear changed stored bytes or revision");
    refused([&]{fresh.associate(1,id,"","");},"stale");
  }
}
void enrolled_uuid_contract() {
  const auto p=policy();Vector v(256);v[0]=1;
  for(const auto* fault:{"none","conflict","unsynced","readback"}) {
    Host host;host.profile=write_snapshot({p.policy,1,{{"legacy-id","Initial name",{{hash("enrolled-origin"),v}}}}},p);
    const auto original=*host.profile;
    aii_voice_capture sample{};sample.samples=64000;sample.embedding[0]=1;
    std::snprintf(sample.embedding_binding,sizeof sample.embedding_binding,"%s",p.policy.embedding_binding.c_str());
    std::snprintf(sample.pcm_sha256,sizeof sample.pcm_sha256,"%s",hash("fresh-speech").c_str());
    if(std::string(fault)=="conflict")host.fail_publish="speaker_registry";
    if(std::string(fault)=="unsynced")host.unsynced="speaker_registry";
    if(std::string(fault)=="readback")host.corrupt_readback="speaker_registry";
    SpeakerRegistryStore store(host.bridge,p);
    if(std::string(fault)!="none") {
      refused([&]{store.observe(&sample,1,1);},std::string(fault)=="conflict"?"not published":std::string(fault)=="unsynced"?"durability":"unresolved");
      continue;
    }
    auto observed=store.observe(&sample,1,1);const auto id=str(field(observed.get(),"speaker_uuid"));
    check(id.size()==36&&str(field(observed.get(),"speaker_id"))=="legacy-id"&&
      str(field(observed.get(),"display_label"))=="Initial name"&&*host.profile==original,"enrolled UUID or label missing; enrollment changed");
    check(str(field(store.observe(&sample,1,1).get(),"reason"))=="same_speaker_on_multiple_tracks",
      "overlap borrowed enrolled UUID twice");
    host.bridge.cancel();host.bridge.begin("later-session");SpeakerRegistryStore restarted(host.bridge,p);
    const auto before=host.publications.size();
    check(str(field(restarted.observe(&sample,2,1).get(),"speaker_uuid"))==id&&host.publications.size()==before,
      "restart reminted enrolled UUID");
    restarted.associate(1,id,"Context name","relationship");
    observed=restarted.observe(&sample,2,2);
    check(str(field(observed.get(),"speaker_uuid"))==id&&str(field(observed.get(),"display_label"))=="Context name",
      "context label not returned with durable UUID");
    const auto list=restarted.list();const auto* row=field(list.get(),"speakers")->child;
    check(flag(field(row,"matching_ready"))&&flag(field(row,"profile_available"))&&
      str(field(row,"enrollment_id"))=="legacy-id","linked enrollment undiscoverable");
    refused([&]{restarted.forget(2,id);},"speaker.remove");
    restarted.associate(2,id,"","");
    observed=restarted.observe(&sample,2,3);
    check(str(field(observed.get(),"speaker_uuid"))==id&&str(field(observed.get(),"display_label"))=="unknown",
      "clearing label changed UUID or omitted explicit unknown label");
    host.profile.reset();host.bridge.cancel();host.bridge.begin("removed-enrollment");
    SpeakerRegistryStore removed(host.bridge,p);
    check(!flag(field(field(removed.list().get(),"speakers")->child,"matching_ready")),"removed enrollment still ready");
    check(!field(removed.observe(&sample,3,1).get(),"speaker_uuid"),"removed enrolled voice inherited old UUID");
    refused([&]{removed.link(3,id,"00000000-0000-4000-8000-000000000001");},"available acoustic profiles");
    removed.forget(3,id);
  }
}
void joint_gallery_margin_contract() {
  const auto p=policy();
  const auto vector=[](double score,size_t axis){Vector v(256);v[0]=score;v[axis]=std::sqrt(1-score*score);return v;};
  for(bool reverse:{false,true})for(bool near:{false,true}) {
    Host host;SpeakerRegistryStore store(host.bridge,p);
    const std::string first="00000000-0000-4000-8000-000000000001",second="00000000-0000-4000-8000-000000000002";
    const double weak=near?.70:.58;
    Speaker winner{reverse?first:"enrolled","winner",{{hash("winner"),vector(.76,1)}}};
    Speaker other{reverse?"enrolled-a":first,"other",{{hash("other"),vector(weak,2)}}};
    Speaker runner{reverse?"enrolled-b":second,"runner",{{hash("runner"),vector(weak-.01,3)}}};
    Snapshot enrolled{p.policy,1,reverse?std::vector<Speaker>{other,runner}:std::vector<Speaker>{winner}};
    SpeakerRegistry registry{2,{p.policy,2,reverse?std::vector<Speaker>{winner}:std::vector<Speaker>{other,runner}}, {}};
    for(size_t i=0;i<registry.profiles.speakers.size();++i){
      auto& s=registry.profiles.speakers[i];s.label=s.id;
      s.samples.push_back({hash(s.id+"corroborating"),s.samples.front().embedding});
      std::sort(s.samples.begin(),s.samples.end(),[](const auto& a,const auto& b){return a.audio_sha256<b.audio_sha256;});
      registry.buckets.push_back({s.id,i+1,{}, {}});
    }
    host.profile=write_snapshot(enrolled,p);host.archives["speaker_registry"]=write_registry(registry,p);
    aii_voice_capture sample{};sample.samples=64000;sample.embedding[0]=1;
    std::snprintf(sample.embedding_binding,sizeof sample.embedding_binding,"%s",p.policy.embedding_binding.c_str());
    std::snprintf(sample.pcm_sha256,sizeof sample.pcm_sha256,"%s",hash("query").c_str());
    const auto result=store.observe(&sample,1,1);
    if(near)check(str(field(result.get(),"reason"))=="cross_gallery_ambiguous"&&!field(result.get(),"speaker_uuid"),"close global competitors accepted");
    else if(reverse)check(field(result.get(),"speaker_uuid")&&str(field(result.get(),"speaker_uuid"))==first,"weak enrolled ambiguity vetoed separated anonymous winner");
    else check(field(result.get(),"speaker_id")&&str(field(result.get(),"speaker_id"))=="enrolled","weak anonymous ambiguity vetoed separated enrolled winner");
    sample.embedding[0]=0;sample.embedding[4]=1;
    const auto unknown=store.observe(&sample,1,2);
    check(!field(unknown.get(),"speaker_uuid")&&!field(unknown.get(),"speaker_id"),"absent speaker became a known identity");
    check(host.publications.size()==size_t(!near&&!reverse),"unexpected registry mutation beyond first enrolled UUID binding");
  }
}
void legacy_singleton_competition_contract() {
  Host host;const auto p=policy();SpeakerRegistryStore store(host.bridge,p);
  const std::string legacy="00000000-0000-4000-8000-000000000001";
  const std::string guest="00000000-0000-4000-8000-000000000002";
  Vector known(256),old(256),other(256);
  known[0]=.70;known[1]=std::sqrt(1-.70*.70);
  old[0]=.75;old[1]=std::sqrt(1-.75*.75);other[2]=1;
  host.profile=write_snapshot({p.policy,1,{{"enrolled","Known",{{hash("guided"),known}}}}},p);
  SpeakerRegistry registry{2,{p.policy,2,{
    {legacy,legacy,{{hash("legacy-one"),old}}},
    {guest,guest,{{hash("guest-one"),other},{hash("guest-two"),other}}}}},
    {{legacy,1,{},{}},{guest,2,{},{}}}};
  for(auto& row:registry.profiles.speakers)
    std::sort(row.samples.begin(),row.samples.end(),[](const auto& a,const auto& b){return a.audio_sha256<b.audio_sha256;});
  const auto original=write_registry(registry,p);host.archives["speaker_registry"]=original;
  const auto listing=store.list();const auto* listed=field(listing.get(),"speakers");
  check(flag(field(listed->child,"profile_available"))&&!flag(field(listed->child,"matching_ready"))&&
    str(field(listed->child,"matching_reason"))=="anonymous_profile_needs_corroboration"&&
    flag(field(listed->child->next,"matching_ready")),"legacy readiness is hidden from the caller");
  aii_voice_capture sample{};sample.samples=64000;sample.embedding[0]=1;
  std::snprintf(sample.embedding_binding,sizeof sample.embedding_binding,"%s",p.policy.embedding_binding.c_str());
  std::snprintf(sample.pcm_sha256,sizeof sample.pcm_sha256,"%s",hash("query").c_str());
  auto result=store.observe(&sample,1,1);
  check(field(result.get(),"speaker_id")&&str(field(result.get(),"speaker_id"))=="enrolled",
    "uncorroborated legacy singleton vetoed explicitly enrolled speaker");
  const auto after_binding=*host.archives.at("speaker_registry");
  const auto bound=read_registry(after_binding,p);
  check(write_snapshot(bound.profiles,p)==write_snapshot(registry.profiles,p)&&bound.buckets.size()==3&&
    host.publications.size()==1,"binding changed existing acoustic profiles");
  sample.embedding[0]=0;sample.embedding[2]=1;result=store.observe(&sample,1,2);
  check(field(result.get(),"speaker_uuid")&&str(field(result.get(),"speaker_uuid"))==guest,
    "corroborated anonymous guest lost identification");
  sample.embedding[2]=0;sample.embedding[3]=1;result=store.observe(&sample,1,3);
  check(!field(result.get(),"speaker_id")&&!field(result.get(),"speaker_uuid"),"absent speaker accepted");
  check(*host.archives.at("speaker_registry")==after_binding&&host.publications.size()==1,
    "readiness projection changed profiles or history");
  const auto enrolled_document=host.profile;
  host.profile=write_snapshot({p.policy,0,{}},p);
  SpeakerRegistryStore reopened(host.bridge,p);
  std::copy(old.begin(),old.end(),sample.embedding);
  const auto legacy_result=reopened.observe(&sample,2,1);
  check(str(field(legacy_result.get(),"reason"))=="anonymous_profile_needs_corroboration"&&
    !field(legacy_result.get(),"speaker_uuid")&&!field(legacy_result.get(),"speaker_id")&&
    *host.archives.at("speaker_registry")==after_binding&&host.publications.size()==1,
    "restart asserted or replaced a legacy singleton without corroboration");
  // Exercise the consumer used by the real worker, not only the producer.
  // A valid unresolved legacy match must not terminate the session.
  Attributions attributions;attributions.begin("legacy-session");
  const FinalKey final{"legacy-session","track-0",1,0,64000};
  attributions.add(1,final,0,true);
  const auto observation=attributions.resolve(1,final,clone(legacy_result.get()));
  check(str(field(observation.get(),"decision"))=="uncertain"&&
    !field(observation.get(),"speaker_uuid")&&
    str(field(field(observation.get(),"match"),"reason"))=="anonymous_profile_needs_corroboration"&&
    attributions.pending()==0,"legacy diagnostic became identity or stranded attribution");
  attributions.add(2,FinalKey{"legacy-session","track-1",2,64000,128000},1,true);
  check(attributions.pending()==1,"unresolved legacy match prevented the next utterance");
  host.profile=enrolled_document;
  registry.profiles.speakers[0].samples.push_back({hash("legacy-two"),old});
  auto& samples=registry.profiles.speakers[0].samples;
  std::sort(samples.begin(),samples.end(),[](const auto& a,const auto& b){return a.audio_sha256<b.audio_sha256;});
  host.archives["speaker_registry"]=write_registry(registry,p);
  std::fill(std::begin(sample.embedding),std::end(sample.embedding),0);sample.embedding[0]=1;
  result=store.observe(&sample,1,4);
  check(str(field(result.get(),"reason"))=="cross_gallery_ambiguous"&&
    !field(result.get(),"speaker_id")&&!field(result.get(),"speaker_uuid"),
    "corroborated competitor bypassed unchanged separation margin");
}
void real_observation_panel(char** argv) {
  const auto read=[](const char* path){std::ifstream in(path,std::ios::binary|std::ios::ate);const auto n=in.tellg();check(in&&n>=0&&n<=(12<<20),"panel file bound");std::string raw(size_t(n),'\0');in.seekg(0);check(bool(in.read(raw.data(),n)),"panel read failed");return raw;};
  const auto p=read_policy(read(argv[2]));Host host;host.profile=read(argv[3]);host.archives["speaker_registry"]=read(argv[4]);
  SpeakerRegistryStore store(host.bridge,p);const auto rows=parse(read(argv[5]));
  check(cJSON_IsArray(rows.get())&&cJSON_GetArraySize(rows.get())<=4096,"panel query bound");
  uint64_t utterance=0;auto out=own(cJSON_CreateArray());
  for(auto* row=rows->child;row;row=row->next) {
    const auto v=decode_vector(str(field(row,"embedding"),2732));aii_voice_capture sample{};
    sample.samples=integer(field(row,"samples"),160000);std::copy(v.begin(),v.end(),sample.embedding);
    std::snprintf(sample.embedding_binding,sizeof sample.embedding_binding,"%s",p.policy.embedding_binding.c_str());
    std::snprintf(sample.pcm_sha256,sizeof sample.pcm_sha256,"%s",str(field(row,"pcm_sha256"),64).c_str());
    auto result=store.observe(&sample,1,++utterance);cJSON_AddItemToArray(out.get(),result.release());
  }
  std::cout<<encode(out)<<'\n';
}
void registry_policy_transition_contract(){
  const auto previous=policy(3,'a'),current=policy();
  Host host;
  SpeakerRegistry old{2,{previous.policy,0,{}},
      {{"00000000-0000-4000-8000-000000000001",1,{{2,"Guest label",""}}, {}}}};
  const auto prior=write_registry(old,previous);
  host.archives["speaker_registry"]=prior;
  SpeakerRegistryStore owner(host.bridge,current,previous);
  auto listed=owner.list();
  check(flag(field(listed.get(),"policy_upgrade_required"))&&
      str(field(field(listed.get(),"speakers")->child,"display_label"))=="Guest label",
      "bound previous registry could not be listed without relabeling");
  check(str(field(owner.observe(nullptr).get(),"reason"))=="speaker_registry_policy_upgrade_required"&&
      *host.archives.at("speaker_registry")==prior,
      "old-policy registry was silently used for current acoustic matching");
  {
    Host removal;
    removal.archives["speaker_registry"]=prior;
    SpeakerRegistryStore bound(removal.bridge,current,previous);
    auto deleted=bound.forget(2,old.buckets[0].uuid);
    check(cJSON_GetArraySize(field(deleted.get(),"speakers"))==0&&
        read_registry(*removal.archives.at("speaker_registry"),previous).buckets.empty(),
        "bound previous registry could not forget its voiceprint/metadata");
  }
  auto renamed=owner.associate(2,old.buckets[0].uuid,"Updated label","");
  check(str(field(renamed.get(),"registry_revision"))=="3"&&
      read_registry(*host.archives.at("speaker_registry"),previous).buckets[0].associations.back().label=="Updated label",
      "older bound registry became unmanageable");
  const auto before=*host.archives.at("speaker_registry");
  host.fail_publish="speaker_registry";
  refused([&]{owner.upgrade_policy();},"not published");
  check(*host.archives.at("speaker_registry")==before,"failed policy upgrade changed registry");
  host.fail_publish.clear();
  auto migrated=owner.upgrade_policy();
  check(!flag(field(migrated.get(),"policy_upgrade_required"))&&
      str(field(migrated.get(),"registry_revision"))=="4"&&
      read_registry(*host.archives.at("speaker_registry"),current).buckets[0].associations.back().label=="Updated label",
      "confirmed registry policy upgrade lost UUID, label or CAS revision");
  check(owner.upgrade_policy().get()!=nullptr&&str(field(owner.list().get(),"registry_revision"))=="4",
      "same-policy retry changed the upgraded registry");
  auto forgotten=owner.forget(4,old.buckets[0].uuid);
  check(cJSON_GetArraySize(field(forgotten.get(),"speakers"))==0,
      "upgraded registry cannot forget biometric metadata");
}
void recovery_contract(){
  const auto p=policy();BoundPolicies bound(p.canonical);
  const auto old=read_policy("{\"calibration_sha256\":\""+std::string(64,'c')+"\",\"embedding_binding\":\""+std::string(64,'d')+"\",\"minimum_enrollment_samples\":1,\"minimum_margin\":0.105,\"threshold\":0.56}");
  for(const std::string kind:{"old","corrupt","empty","absent"}){
    Host host;
    if(kind=="old")host.profile=write_snapshot({old.policy,9,{}},old);
    else if(kind!="absent")host.profile=kind=="empty"?"":"{broken";
    Vector v(256);v[0]=1;
    const auto old_capture=make_pending_capture(hash("old request"),1,32000,old.policy.embedding_binding,{hash("old recording"),v});
    host.pending=write_captures({3,{old_capture}},old.policy.embedding_binding);
    const auto before=inspect_uid(host.bridge,bound);
    check(before.profile_state==(kind=="old"?"incompatible":kind=="absent"?"absent":"corrupt"),"recovery misclassified profile");
    check(before.captures_state=="incompatible","old capture was silently relabelled");
    auto result=recover_uid(host.bridge,bound,before.profile_hash(),before.captures_hash(),hash("confirmed"));
    const auto archive=str(field(result.get(),"recovery_archive_sha256"),64);
    check(host.archives.at("recovery:"+archive).has_value(),"missing archive");
    auto saved=parse(*host.archives.at("recovery:"+archive));
    if(before.profile_absent)check(cJSON_IsNull(field(saved.get(),"enrollment_b64")),"absence fabricated old bytes");
    else check(decode_base64(field(saved.get(),"enrollment_b64")->valuestring,8u<<20)==before.profile,"original corrupt/old profile not preserved");
    check(decode_base64(str(field(saved.get(),"captures_b64"),100000),65536)==before.captures,"old capture not preserved");
    check(bound.read(*host.profile).speakers.empty()&&read_captures(*host.pending,p.policy.embedding_binding).captures.empty(),"recovery did not establish current empty stores");
    CaptureEnrollment flow(host.bridge,p);auto c=evidence();flow.retain(c,hash("fresh"));
    auto enrolled=flow.confirm(c.id,"sam","Sam",hash("fresh confirmation"));
    check(enrolled.enrollment_durable&&bound.read(*host.profile).speakers.size()==1,"fresh enrollment after recovery failed");
    refused([&]{recover_uid(host.bridge,bound,before.profile_hash(),before.captures_hash(),hash("stale confirmation"));},"changed since confirmation");
  }
  for(const std::string fault:{"read","archive-failed","archive-unsynced","captures","enrollment"}){
    Host host;host.profile="corrupt profile";host.pending="corrupt captures";
    auto before=inspect_uid(host.bridge,bound);
    if(fault=="read")host.denied_read="enrollment";
    else if(fault=="archive-failed")host.fail_publish="recovery";
    else if(fault=="archive-unsynced")host.unsynced="recovery";
    else host.fail_publish=fault;
    refused([&]{recover_uid(host.bridge,bound,before.profile_hash(),before.captures_hash(),hash("act"));},
        fault=="read"?"unavailable":fault=="archive-failed"?"not published":fault=="archive-unsynced"?"original stores unchanged":"originals preserved");
    check(host.profile==before.profile,"failed recovery altered original profile");
    if(fault!="enrollment")check(host.pending==before.captures,"failed archive/capture publication changed pending evidence");
    if(fault=="read")check(host.publications.empty(),"unreadable treated as absent");
    if(fault=="captures"||fault=="enrollment"){
      // Inspection's typed-absent registry read leaves an empty test-host slot.
      check(host.archives.size()==2&&!host.archives.at("speaker_registry")&&host.archives.begin()->second.has_value(),
          "recovery failure lost original archive");
      auto preserved=parse(*host.archives.begin()->second);
      check(decode_base64(str(field(preserved.get(),"enrollment_b64")),1000)==before.profile&&
          decode_base64(str(field(preserved.get(),"captures_b64")),1000)==before.captures,"failed recovery lost original bytes");
    }
  }
}
// Exact bindings of earlier UID models from this repository's history: the
// ResNet152-LM config 0.1.0-beta.5 shipped, the ECAPA b0e7732c config whose
// projection 10f8fe8 replaced, and the first SAM-ResNet34 contract. Written
// out here so the test does not trust the header.
const std::string resnet152_binding="2575d15495d0e1cf17a2a12b0163dec0639db6218b3fe810e70369002d3898da";
const std::string replaced_ecapa_binding="ffe5c3d33b41fef374cc147df0061adc25e56ef35b44ed53977e906dac4e29f1";
const std::string samresnet34_binding="c61bbdf12d5b69632b12776c23edc2e7155a9e0a028576eb41e509ca2bfb6d6e";
const char* const earlier_rule="\"minimum_enrollment_samples\":3,\"minimum_margin\":0.105,\"threshold\":0.56";
std::string policy_bytes(const std::string& binding,const char* rule){
  return "{\"calibration_sha256\":\""+std::string(64,'c')+"\",\"embedding_binding\":\""+binding+"\","+rule+"}";
}
// Stored bytes assembled by hand, not by the writers under test: a codec that
// misreads an earlier binding must not also be trusted to build its fixture.
std::string stored_enrollment(const std::string& binding,size_t dimensions,double value=1){
  Vector v(dimensions);v[0]=value;
  return "{\"policy\":"+policy_bytes(binding,earlier_rule)+",\"revision\":2,\"speakers\":[{\"id\":\"sam\",\"label\":\"Sam\","
    "\"samples\":[{\"audio_sha256\":\""+hash("earlier enrollment")+"\",\"embedding_f64le_b64\":\""+encode_vector(v)+"\"}]}]}";
}
std::string stored_captures(const std::string& binding,size_t dimensions,double value=1){
  Vector v(dimensions);v[1]=value;
  const auto body="{\"audio_sha256\":\""+hash("earlier capture")+"\",\"created_ms\":1,\"embedding_binding\":\""+binding+
    "\",\"embedding_f64le_b64\":\""+encode_vector(v)+"\",\"request_id\":\""+hash("earlier request")+"\",\"samples\":160000}";
  return "{\"captures\":[{\"id\":\""+hash("aiii.uid.pending-capture\n"+body)+"\",\"recording\":"+body+"}],\"revision\":3}";
}
void earlier_model_contract(){
  const auto runtime=read_policy(policy_bytes(ecapa_binding,"\"minimum_enrollment_samples\":1,\"minimum_margin\":0.14,\"threshold\":0.4"));
  BoundPolicies bound(runtime.canonical);
  struct Earlier{const char* name;std::string binding;size_t dimensions;};
  for(const auto& model:std::vector<Earlier>{{"ResNet152-LM",resnet152_binding,256},
      {"original ECAPA projection",replaced_ecapa_binding,192},{"SAM-ResNet34",samresnet34_binding,256}}) {
    try {
      Host host;host.profile=stored_enrollment(model.binding,model.dimensions);
      host.pending=stored_captures(model.binding,model.dimensions);
      const auto profile=*host.profile,captures=*host.pending;
      const auto seen=inspect_uid(host.bridge,bound);
      check(seen.profile_state=="incompatible"&&seen.profile_model==model.binding,"earlier-model enrollment not reported incompatible");
      check(seen.captures_state=="incompatible","earlier-model capture not reported incompatible");
      const auto report=seen.report();
      check(flag(field(report.get(),"required"))&&str(field(report.get(),"stored_embedding_binding"),64)==model.binding,
          "speaker.list recovery does not ask for re-enrollment");
      refused([&]{(void)bound.read(profile);},"not bound by this runtime");
      // The live owner fails closed; the session reports enrollment_unavailable.
      SpeakerRegistryStore store(host.bridge,runtime);
      aii_voice_capture sample{};sample.samples=64000;sample.embedding[0]=1;
      std::snprintf(sample.embedding_binding,sizeof sample.embedding_binding,"%s",ecapa_binding);
      std::snprintf(sample.pcm_sha256,sizeof sample.pcm_sha256,"%s",hash("live speech").c_str());
      char out[8192];size_t written=1;
      check(SpeakerRegistryStore::callback_at(&store,1,1,&sample,out,sizeof out,&written)==AII_VOICE_FAILED&&!written,
          "earlier-model enrollment reached matching");
      check(*host.profile==profile&&*host.pending==captures&&host.publications.empty(),"earlier-model evidence was rewritten");
      // Only operator-confirmed recovery replaces them, after archiving exact bytes.
      auto result=recover_uid(host.bridge,bound,seen.profile_hash(),seen.captures_hash(),hash("re-enroll"));
      auto saved=parse(*host.archives.at("recovery:"+str(field(result.get(),"recovery_archive_sha256"),64)));
      check(decode_base64(field(saved.get(),"enrollment_b64")->valuestring,8u<<20)==profile&&
          decode_base64(str(field(saved.get(),"captures_b64"),100000),65536)==captures,"recovery lost earlier-model bytes");
    }catch(const std::exception& e){throw std::runtime_error(std::string(model.name)+": "+e.what());}
  }
  // Bytes contradicting their own model binding stay corrupt, not incompatible.
  struct Damaged{const char* name;std::string profile,captures;};
  for(const auto& damaged:std::vector<Damaged>{
      {"192-coordinate ResNet152-LM",stored_enrollment(resnet152_binding,192),stored_captures(resnet152_binding,192)},
      {"256-coordinate original ECAPA",stored_enrollment(replaced_ecapa_binding,256),stored_captures(replaced_ecapa_binding,256)},
      {"non-unit original ECAPA",stored_enrollment(replaced_ecapa_binding,192,2),stored_captures(replaced_ecapa_binding,192,2)}}) {
    Host host;host.profile=damaged.profile;host.pending=damaged.captures;
    const auto seen=inspect_uid(host.bridge,bound);
    if(seen.profile_state!="corrupt"||seen.captures_state!="corrupt"||!seen.profile_model.empty()||!seen.needs_recovery())
      throw std::runtime_error(std::string(damaged.name)+": damaged UID store not reported corrupt");
  }
}
const char* const runtime_rule="\"minimum_enrollment_samples\":1,\"minimum_margin\":0.14,\"threshold\":0.4";
// write_registry's exact layout, assembled by hand like the fixtures above.
std::string stored_registry(const std::string& binding,size_t dimensions,const char* rule=earlier_rule){
  Vector v(dimensions);v[2]=1;const std::string id="0b6a1c2e-8d4f-4a6b-9c3d-2e1f0a9b8c7d";
  const auto profiles="{\"policy\":"+policy_bytes(binding,rule)+",\"revision\":1,\"speakers\":[{\"id\":\""+id+"\",\"label\":\""+id+
    "\",\"samples\":[{\"audio_sha256\":\""+hash("earlier anonymous")+"\",\"embedding_f64le_b64\":\""+encode_vector(v)+"\"}]}]}";
  std::string quoted;
  for(const char c:profiles){if(c=='"')quoted+='\\';quoted+=c;}
  return "{\"schema\":1,\"revision\":1,\"profile_document\":\""+quoted+"\",\"buckets\":[{\"uuid\":\""+id+
    "\",\"created_revision\":1,\"associations\":[]}]}";
}
Json observe_live(SpeakerRegistryStore& store,uint64_t utterance,aii_voice_result expected_result){
  aii_voice_capture sample{};sample.samples=64000;sample.embedding[5]=1;
  std::snprintf(sample.embedding_binding,sizeof sample.embedding_binding,"%s",ecapa_binding);
  std::snprintf(sample.pcm_sha256,sizeof sample.pcm_sha256,"%s",hash("live speech "+std::to_string(utterance)).c_str());
  char out[8192];size_t written=0;
  check(SpeakerRegistryStore::callback_at(&store,1,utterance,&sample,out,sizeof out,&written)==expected_result,
      "speaker observation result differs");
  return expected_result==AII_VOICE_OK?parse(std::string(out,written)):null();
}
// 0.1.0-beta.5 also kept an anonymous speaker registry under ResNet152-LM.
// Confirmed recovery must name, archive and replace it with the enrollment,
// or identification stays unavailable after the speakers re-enroll.
void earlier_registry_contract(){
  const auto runtime=read_policy(policy_bytes(ecapa_binding,runtime_rule));BoundPolicies bound(runtime.canonical);
  Host host;host.profile=stored_enrollment(resnet152_binding,256);host.pending=stored_captures(resnet152_binding,256);
  host.archives["speaker_registry"]=stored_registry(resnet152_binding,256);
  const auto profile=*host.profile,captures=*host.pending,registry=*host.archives.at("speaker_registry");
  const auto seen=inspect_uid(host.bridge,bound);
  const auto report=seen.report();
  const auto* state=field(report.get(),"speaker_registry_state");
  check(state&&str(state)=="incompatible"&&str(field(report.get(),"speaker_registry_sha256"),64)==hash(registry)&&
      str(field(report.get(),"speaker_registry_stored_embedding_binding"),64)==resnet152_binding&&flag(field(report.get(),"required")),
      "beta.5 speaker registry not reported for recovery");
  SpeakerRegistryStore store(host.bridge,runtime);
  refused([&]{(void)store.list();},"speaker.list reports UID recovery");
  (void)observe_live(store,1,AII_VOICE_FAILED);
  // A confirmation that did not see the registry changes nothing.
  refused([&]{recover_uid(host.bridge,bound,seen.profile_hash(),seen.captures_hash(),hash("two digests"));},"registry also needs recovery");
  check(*host.profile==profile&&*host.pending==captures&&*host.archives.at("speaker_registry")==registry&&host.publications.empty(),
      "unconfirmed or partial recovery changed beta.5 evidence");
  auto result=recover_uid(host.bridge,bound,seen.profile_hash(),seen.captures_hash(),hash("all digests"),seen.registry_hash());
  auto kept=parse(*host.archives.at("recovery:"+str(field(result.get(),"speaker_registry_recovery_archive_sha256"),64)));
  check(decode_base64(field(kept.get(),"speaker_registry_b64")->valuestring,8u<<20)==registry&&
      str(field(kept.get(),"speaker_registry_sha256"),64)==hash(registry),"recovery lost the beta.5 speaker registry");
  check(read_registry(*host.archives.at("speaker_registry"),runtime).buckets.empty()&&
      str(field(store.list().get(),"registry_revision"))=="0","recovery did not start an empty current registry");
  CaptureEnrollment flow(host.bridge,runtime);Vector v(192);v[5]=1;
  const auto capture=make_pending_capture(hash("re-enroll request"),1,160000,ecapa_binding,{hash("re-enroll audio"),v});
  flow.retain(capture,hash("re-enroll retain"));
  check(flow.confirm(capture.id,"sam","Sam",hash("re-enroll confirm")).enrollment_durable,"re-enrollment failed");
  const auto known=observe_live(store,2,AII_VOICE_OK);
  check(str(field(known.get(),"outcome"))=="known"&&str(field(known.get(),"speaker_id"))=="sam",
      "identification did not resume after recovery and re-enrollment");
  // A ready current registry is not reported; the two-digest reset keeps it.
  for(const std::string kind:{"current","corrupt"}){
    Host other;other.profile=stored_enrollment(resnet152_binding,256);
    other.archives["speaker_registry"]=kind=="current"?stored_registry(ecapa_binding,192,runtime_rule):"{broken";
    const auto before=*other.archives.at("speaker_registry");
    const auto observed=inspect_uid(other.bridge,bound);
    if(kind=="current"){
      check(observed.registry_state=="ready"&&!field(observed.report().get(),"speaker_registry_sha256"),"current registry reported for recovery");
      refused([&]{recover_uid(other.bridge,bound,observed.profile_hash(),observed.captures_hash(),hash("stale"),hash(before));},"changed since confirmation");
      (void)recover_uid(other.bridge,bound,observed.profile_hash(),observed.captures_hash(),hash("two digests"));
      check(*other.archives.at("speaker_registry")==before,"two-digest recovery changed a current registry");
    }else{
      check(observed.registry_state=="corrupt"&&observed.registry_model.empty()&&observed.needs_recovery(),"corrupt registry not reported");
      (void)recover_uid(other.bridge,bound,observed.profile_hash(),observed.captures_hash(),hash("all digests"),observed.registry_hash());
      check(read_registry(*other.archives.at("speaker_registry"),runtime).buckets.empty(),"corrupt registry not replaced");
    }
  }
}
// A later process: a new broker session and new owners over exactly the bytes
// an interrupted recovery left behind.
void carry(Host& next,const Host& old){next.profile=old.profile;next.pending=old.pending;next.archives=old.archives;}
std::string archive_field(const Host& host,const std::string& id,const char* name){
  const auto slot=host.archives.find("recovery:"+id);
  check(slot!=host.archives.end()&&slot->second&&hash(*slot->second)==id,"recovery archive missing or not content addressed");
  return str(field(parse(*slot->second).get(),name),16u<<20);
}
// Confirmed beta.5 recovery interrupted at each registry step: its own
// archive, then the empty registry's CAS, receipt, durability and readback.
// No refusal claims an archive or reset it has not proven, every original
// byte stays recoverable, and a later process completes recovery from a
// fresh confirmation of exactly what it inspects.
void earlier_registry_failure_contract(){
  const auto runtime=read_policy(policy_bytes(ecapa_binding,runtime_rule));BoundPolicies bound(runtime.canonical);
  const auto profile=stored_enrollment(resnet152_binding,256),captures=stored_captures(resnet152_binding,256),
      registry=stored_registry(resnet152_binding,256);
  const auto beta5=[&](Host& host){host.profile=profile;host.pending=captures;host.archives["speaker_registry"]=registry;};
  // Archives are addressed by content: an uninterrupted recovery of the same
  // bytes names both, so a fault can target only the registry's own archive.
  std::string enrollment_archive,registry_archive;
  {
    Host clean;beta5(clean);const auto seen=inspect_uid(clean.bridge,bound);
    const auto done=recover_uid(clean.bridge,bound,seen.profile_hash(),seen.captures_hash(),hash("uninterrupted"),seen.registry_hash());
    enrollment_archive=str(field(done.get(),"recovery_archive_sha256"),64);
    registry_archive=str(field(done.get(),"speaker_registry_recovery_archive_sha256"),64);
    check(enrollment_archive!=registry_archive,"speaker registry shares the enrollment archive");
  }
  const auto originals=[&](const Host& host,bool registry_archived){
    check(decode_base64(archive_field(host,enrollment_archive,"enrollment_b64"),8u<<20)==profile&&
        decode_base64(archive_field(host,enrollment_archive,"captures_b64"),65536)==captures&&
        archive_field(host,enrollment_archive,"enrollment_sha256")==hash(profile)&&
        archive_field(host,enrollment_archive,"captures_sha256")==hash(captures),"archive lost beta.5 enrollment or captures");
    if(registry_archived)check(decode_base64(archive_field(host,registry_archive,"speaker_registry_b64"),8u<<20)==registry&&
        archive_field(host,registry_archive,"speaker_registry_sha256")==hash(registry),"archive lost the beta.5 speaker registry");
  };
  const auto has=[](const std::string& text,const char* part){return text.find(part)!=std::string::npos;};
  for(const std::string fault:{"archive-failed","archive-unsynced","archive-lost","registry-conflict","registry-unsynced",
      "registry-unsynced-reverted","registry-lost","registry-lost-reverted","registry-readback"}){
    try{
      Host host;beta5(host);const auto seen=inspect_uid(host.bridge,bound);
      check(seen.registry_recovery()&&seen.registry_hash()==hash(registry),"beta.5 speaker registry not reported");
      const bool archive=has(fault,"archive-"),reverted=has(fault,"-reverted");
      const auto target=archive?"recovery:"+registry_archive:std::string("speaker_registry");
      if(fault=="archive-failed"||fault=="registry-conflict")host.fail_publish=target;
      if(has(fault,"unsynced"))host.unsynced=target;
      if(has(fault,"lost"))host.lost_receipt=target;
      if(fault=="registry-readback")host.corrupt_readback=target;
      const auto why=refusal([&]{recover_uid(host.bridge,bound,seen.profile_hash(),seen.captures_hash(),hash("interrupted"),seen.registry_hash());});
      // Each refusal names the store it is about, never the enrollment.
      contains(why,fault=="archive-failed"?"recovery archive generation/digest conflict; not published":
          fault=="registry-conflict"?"speaker registry generation/digest conflict; not published":
          fault=="archive-unsynced"?"recovery archive durability unresolved; original stores unchanged":
          has(fault,"unsynced")?"speaker registry reset durability unresolved":
          archive?"recovery archive publication/readback unresolved":"speaker registry publication/readback unresolved");
      if(archive){
        // Nothing was reset before both archives were durable, so the
        // originals are untouched in place. The refusal names the durable
        // enrollment/captures archive, which holds biometric bytes, and
        // claims no registry archive.
        contains(why,"enrollment and captures preserved in uid/recovery-"+enrollment_archive+".json");
        check(!has(why,"originals preserved")&&!has(why,registry_archive.c_str()),"refusal claimed an unproven registry archive");
        check(host.profile==profile&&host.pending==captures&&host.archives.at("speaker_registry")==registry,
            "a store changed before the registry archive was durable");
        check(host.publications==std::vector<std::string>{"recovery:"+enrollment_archive,"recovery:"+registry_archive},
            "a store was reset before the registry archive was durable");
        const auto slot=host.archives.find("recovery:"+registry_archive);
        check((fault=="archive-failed")==(slot==host.archives.end()||!slot->second),"registry archive outcome differs from its fault");
        originals(host,fault!="archive-failed");
      }else{
        contains(why,"originals preserved in uid/recovery-"+enrollment_archive+".json and uid/recovery-"+registry_archive+".json");
        check(bound.read(*host.profile).speakers.empty()&&read_captures(*host.pending,runtime.policy.embedding_binding).captures.empty(),
            "enrollment and captures were not reset before the registry");
        check(host.publications==std::vector<std::string>{"recovery:"+enrollment_archive,"recovery:"+registry_archive,
            "captures","enrollment","speaker_registry"},"registry reset out of order");
        const auto& left=*host.archives.at("speaker_registry");
        check(fault=="registry-conflict"?left==registry:read_registry(left,runtime).buckets.empty(),"registry publication outcome differs from its fault");
        originals(host,true);
        // A registry that does not read back is neither absent nor ready.
        if(fault=="registry-readback")refused([&]{(void)inspect_uid(host.bridge,bound);},"digest mismatch");
      }
      Host next;carry(next,host);
      // An unsynced or unacknowledged rename may not survive the crash.
      // Restoring the earlier registry is explicit fault injection.
      if(reverted)next.archives["speaker_registry"]=registry;
      const auto again=inspect_uid(next.bridge,bound);
      if(!archive){
        refused([&]{recover_uid(next.bridge,bound,seen.profile_hash(),seen.captures_hash(),hash("stale"),seen.registry_hash());},
            "changed since confirmation");
        check(next.publications.empty(),"stale confirmation changed a store");
      }
      if(!archive&&fault!="registry-conflict"&&!reverted){
        // The empty registry stands: speaker.list reports nothing to recover
        // and asks for no confirmation.
        check(!again.needs_recovery()&&again.registry_state=="ready"&&!field(again.report().get(),"speaker_registry_sha256"),
            "recovered speaker registry still reported");
      }else{
        check(again.registry_recovery()&&again.registry_hash()==hash(registry)&&
            (archive?again.profile_hash()==seen.profile_hash()&&again.captures_hash()==seen.captures_hash():
                again.profile_state=="ready"&&again.captures_state=="ready"),"inspection differs from the bytes left behind");
        SpeakerRegistryStore early(next.bridge,runtime);(void)observe_live(early,1,AII_VOICE_FAILED);
        const auto done=recover_uid(next.bridge,bound,again.profile_hash(),again.captures_hash(),hash("fresh "+fault),again.registry_hash());
        check(flag(field(done.get(),"recovery_durable")),"fresh recovery archive not durable");
        for(const char* name:{"publication","capture_publication","speaker_registry_publication"}){
          const auto* receipt=field(done.get(),name);
          check(flag(field(receipt,"durable"))&&flag(field(receipt,"readback_verified")),"fresh recovery claimed an unproven reset");
        }
        const auto first=str(field(done.get(),"recovery_archive_sha256"),64);
        check(str(field(done.get(),"speaker_registry_recovery_archive_sha256"),64)==registry_archive&&(first==enrollment_archive)==archive,
            "fresh recovery archived other bytes");
        check(next.publications==std::vector<std::string>{"recovery:"+first,"recovery:"+registry_archive,"captures","enrollment","speaker_registry"},
            "fresh recovery skipped an archive attestation or reset");
      }
      originals(next,true);
      check(bound.read(*next.profile).speakers.empty()&&read_registry(*next.archives.at("speaker_registry"),runtime).buckets.empty(),
          "recovery did not leave empty current stores");
      CaptureEnrollment flow(next.bridge,runtime);Vector v(192);v[5]=1;
      const auto capture=make_pending_capture(hash("re-enroll request"),1,160000,ecapa_binding,{hash("re-enroll audio"),v});
      flow.retain(capture,hash("re-enroll retain"));
      check(flow.confirm(capture.id,"sam","Sam",hash("re-enroll confirm")).enrollment_durable,"re-enrollment failed");
      SpeakerRegistryStore store(next.bridge,runtime);const auto known=observe_live(store,2,AII_VOICE_OK);
      check(str(field(known.get(),"outcome"))=="known"&&str(field(known.get(),"speaker_id"))=="sam",
          "identification did not resume after recovery and re-enrollment");
    }catch(const std::exception& e){throw std::runtime_error(fault+": "+e.what());}
  }
}
}
int main(int argc,char** argv){try{
  if(argc==6&&std::string(argv[1])=="--observation-panel")real_observation_panel(argv);
  else if(argc==3)process_step(argv[1],argv[2]);
  else{
  check(argc==1,"unexpected arguments");inherited_label_clear_contract();enrolled_uuid_contract();legacy_singleton_competition_contract();joint_gallery_margin_contract();complete_and_reopen();interrupted_publication();interrupted_cleanup();refusals();recovery_contract();earlier_model_contract();earlier_registry_contract();earlier_registry_failure_contract();registry_contract();registry_management_does_not_discard_live_observation();registry_policy_transition_contract();
  std::cout<<"guided enrollment publication: closed-mic confirmation, profile-first durable readback, explicit restart reconciliation, no duplicated identity, cleanup uncertainty and fail-closed reads PASS\n";
  }
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
