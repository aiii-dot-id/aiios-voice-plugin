#include "pending_captures.h"
#include "../native/vendor/picosha2/picosha2.h"
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
using namespace aii::uid;
namespace {
const std::string binding(64,'b');
void check(bool ok,const char* reason){if(!ok)throw std::runtime_error(reason);}
template<class F> void refused(F fn,const char* needle) {
  try{fn();}catch(const std::exception& error){
    if(std::string(error.what()).find(needle)==std::string::npos)
      throw std::runtime_error(std::string("unexpected refusal: ")+error.what());
    return;
  }
  throw std::runtime_error(std::string("accepted invalid capture: ")+needle);
}
PendingCapture capture(unsigned n) {
  Vector v{};v.at(n%256)=1;v.at((n+1)%256)=-0.;
  const auto suffix=std::to_string(n);
  // Deliberately old timestamp: pending explicit enrollment is not subject to
  // the former live-session ten-minute expiry. No clock is passed to read.
  return make_pending_capture(picosha2::hash256_hex_string("request"+suffix),1,160000,binding,
      {picosha2::hash256_hex_string("audio"+suffix),v});
}
std::string one() {
  return retain_capture(write_captures({},binding),capture(0),binding).snapshot;
}
void verify_reload(const std::string& bytes) {
  const auto loaded=read_captures(bytes,binding);
  const auto info=list_captures(loaded);
  check(loaded.revision==1&&info.size()==1&&info[0].id==capture(0).id&&
      info[0].created_ms==1&&info[0].samples==160000,"restarted capture unavailable/expired");
  const auto selected=select_capture(loaded,info[0].id);
  check(selected.recording.audio_sha256==capture(0).recording.audio_sha256&&
      encode_vector(selected.recording.embedding)==encode_vector(capture(0).recording.embedding),
      "restarted capture evidence changed");
  check(write_captures(loaded,binding)==bytes,"restarted capture bytes changed");
}
void contracts() {
  const auto empty=write_captures({},binding);
  const auto c=capture(0);
  const auto prepared=retain_capture(empty,c,binding);
  check(prepared.base_sha256==picosha2::hash256_hex_string(empty)&&prepared.revision==1,
      "capture publication lost CAS base/revision");
  verify_reload(prepared.snapshot);
  auto retry=retain_capture(prepared.snapshot,c,binding);
  check(retry.snapshot==prepared.snapshot&&retry.revision==1,"exact capture retry duplicated evidence");
  const auto removed=discard_capture(prepared.snapshot,c.id,binding);
  check(read_captures(removed.snapshot,binding).captures.empty()&&removed.revision==2,"discard failed");
  check(discard_capture(removed.snapshot,c.id,binding).snapshot==removed.snapshot,"discard retry changed revision");
  refused([&]{select_capture(read_captures(removed.snapshot,binding),c.id);},"selected pending capture unavailable");
  auto other=capture(1);other.request_id=c.request_id;
  other=make_pending_capture(other.request_id,other.created_ms,other.samples,binding,other.recording);
  refused([&]{retain_capture(prepared.snapshot,other,binding);},"request already has different evidence");
  other=capture(1);other.recording=c.recording;
  other=make_pending_capture(other.request_id,other.created_ms,other.samples,binding,other.recording);
  refused([&]{retain_capture(prepared.snapshot,other,binding);},"duplicate capture recording");
  std::string full=empty;
  for(unsigned i=0;i<pending_capture_capacity;++i)full=retain_capture(full,capture(i),binding).snapshot;
  check(full.size()<=pending_capture_max_bytes&&read_captures(full,binding).captures.size()==16,"capacity unexpectedly smaller than advertised");
  refused([&]{retain_capture(full,capture(16),binding);},"store full");
  check(retain_capture(full,capture(0),binding).snapshot==full,"full-store retry evicted capture");
  auto changed=c;changed.samples++;
  refused([&]{write_captures({1,{changed}},binding);},"identity/evidence changed");
  changed=c;changed.recording.embedding[0]=.5;
  refused([&]{write_captures({1,{changed}},binding);},"unit vector");
  changed=c;changed.recording.embedding[0]=std::numeric_limits<double>::quiet_NaN();
  refused([&]{write_captures({1,{changed}},binding);},"not finite");
  refused([&]{read_captures(prepared.snapshot,std::string(64,'c'));},"binding differs");
  refused([&]{read_captures("",binding);},"byte bound");
  refused([&]{read_captures(std::string(65537,' '),binding);},"byte bound");
  refused([&]{read_captures(prepared.snapshot+" ",binding);},"not canonical");
  refused([&]{read_captures("{\"captures\":[],\"revision\":0,\"extra\":true}",binding);},"fields differ");
  refused([&]{read_captures("{\"captures\":[],\"revision\":0,\"revision\":0}",binding);},"duplicate");
  for(uint64_t n:{uint64_t(31919),uint64_t(480001)})
    refused([&]{make_pending_capture(c.request_id,1,n,binding,c.recording);},"bounded context");
  for(uint64_t t:{uint64_t(0),uint64_t(9007199254740992ULL)})
    refused([&]{make_pending_capture(c.request_id,t,160000,binding,c.recording);},"timestamp invalid");
  refused([&]{make_pending_capture("bad",1,160000,binding,c.recording);},"digest/nonce");
  auto exhausted=read_captures(empty,binding);exhausted.revision=9007199254740991ULL;
  refused([&]{retain_capture(write_captures(exhausted,binding),c,binding);},"revision exhausted");
  // Vector codec extraction must preserve every bit, including negative zero,
  // subnormals, and values not rounded through decimal JSON.
  Vector v{};v[0]=1;v[1]=-0.;v[2]=std::numeric_limits<double>::denorm_min();v[3]=.123456789012345;
  const auto decoded=decode_vector(encode_vector(v));
  check(std::memcmp(v.data(),decoded.data(),sizeof(v))==0,"shared vector codec changed bits");
  refused([&]{decode_vector("AAAA");},"embedding extent differs");
}
}
int main(int argc,char** argv){try {
  if(argc==3&&std::string(argv[1])=="save"){
    std::ofstream out(argv[2],std::ios::binary|std::ios::trunc);out<<one();out.close();
    check(bool(out),"test broker save failed");
    std::cout<<"pending capture saved by test broker; this is not an fsync durability claim\n";
  }else if(argc==3&&std::string(argv[1])=="reload"){
    std::ifstream in(argv[2],std::ios::binary);check(bool(in),"test broker file missing");
    std::string bytes((std::istreambuf_iterator<char>(in)),{});check(!in.bad(),"test broker read failed");
    verify_reload(bytes);std::cout<<"fresh-process capture reload and exact handle/evidence PASS\n";
  }else{check(argc==1,"unknown test mode");contracts();
    std::cout<<"pending captures: canonical evidence, bounded custody, no expiry/eviction, CAS, retry and discard PASS\n";}
}catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}}
