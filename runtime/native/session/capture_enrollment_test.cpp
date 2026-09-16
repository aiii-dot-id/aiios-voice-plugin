#include "capture_enrollment.h"
#include "../vendor/picosha2/picosha2.h"
#include <iostream>
#include <optional>
#include <filesystem>
#include <fstream>

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
PolicyDocument policy(unsigned floor=1){return read_policy("{\"calibration_sha256\":\""+std::string(64,'c')+
  "\",\"embedding_binding\":\""+std::string(64,'b')+"\",\"minimum_enrollment_samples\":"+std::to_string(floor)+
  ",\"minimum_margin\":0.105,\"threshold\":0.56}");}
PendingCapture evidence(unsigned number=0){
  Vector v{};v.at(number)=.6;v.at(number+1)=.8;
  return make_pending_capture(hash("request"+std::to_string(number)),1,160000,std::string(64,'b'),
      {hash("audio"+std::to_string(number)),v});
}
// Test host, not another production store: implements the existing bridge's
// two fixed files, staging, generation comparison, receipts and readback.
struct Host {
  std::optional<std::string> profile,pending;
  std::map<std::string,std::string> staged;
  std::vector<std::string> publications;
  std::string fail_publish,unsynced,lost_receipt,bad_read,denied_read,corrupt_readback;
  std::filesystem::path disk;
  unsigned reads=0;
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
    check(key=="captures"||key=="enrollment","arbitrary private resource");
    auto& current=key=="captures"?pending:profile;
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
      if(fail_publish==key)error("generation changed","FS_GENERATION_MISMATCH");
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
          put(v,"replaced",boolean(replaced));put(v,"durable",boolean(unsynced!=key));
          put(v,"durability",string(unsynced==key?"unknown":"synced"));put(reply,"value",std::move(v));
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
  auto changed=c.recording;changed.embedding={};changed.embedding[9]=1;
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
    Vector unknown{};unknown[20]=1;
    const auto other=identify(snapshot,p.policy,unknown,c.embedding_binding);
    check(snapshot.revision==1&&snapshot.speakers.size()==1&&snapshot.speakers[0].samples.size()==1&&
        known.speaker_id=="sam"&&other.speaker_id.empty(),"restarted profile changed known/unknown identity");
  }else throw std::invalid_argument("unknown process step");
  std::cout<<"fresh-process "<<mode<<" PASS; fixture broker, no host durability or acoustic claim\n";
}
}
int main(int argc,char** argv){try{
  if(argc==3)process_step(argv[1],argv[2]);
  else{
  check(argc==1,"unexpected arguments");complete_and_reopen();interrupted_publication();interrupted_cleanup();refusals();
  std::cout<<"guided enrollment publication: closed-mic confirmation, profile-first durable readback, explicit restart reconciliation, no duplicated identity, cleanup uncertainty and fail-closed reads PASS\n";
  }
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
