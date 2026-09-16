// Test composition only: enroll public recordings in memory, never an identity.
#include "native_speaker.h"
#include <chrono>
#include "worker_json.h"
#include <fstream>
#include <iostream>
using namespace aii::voice;
using namespace aii::voice::wire;
std::string bytes(const char* path,size_t limit) {
  std::ifstream f(path,std::ios::binary|std::ios::ate);
  const auto n=f.tellg();require(f&&n>0&&uint64_t(n)<=limit,"bounded fixture required");
  std::string raw(size_t(n),'\0');f.seekg(0);f.read(raw.data(),n);require(bool(f),"fixture read failed");return raw;
}
std::vector<float> pcm(const char* path) {
  const auto raw=bytes(path,960000);require(raw.size()%2==0,"s16le required");std::vector<float> out(raw.size()/2);
  for(size_t i=0;i<out.size();++i){const uint16_t v=uint8_t(raw[i*2])|(uint16_t(uint8_t(raw[i*2+1]))<<8);out[i]=float(int16_t(v))/32768.f;}
  return out;
}
int main(int argc,char** argv){try {
  require(argc==8,"model policy three-enrollment-PCM known-PCM unknown-PCM required");
  const auto policy=aii::uid::read_policy(bytes(argv[2],4096));
  const auto empty=aii::uid::write_snapshot({policy.policy,0,{}},policy);
  auto current=empty;bool unavailable=false;
  NativeSpeaker uid(argv[1],"cpu",policy.policy,[&]{if(unavailable)throw std::runtime_error("fixture profile absent");return aii::uid::read_snapshot(current,policy);});
  uid.open();
  std::vector<std::vector<float>> selected{pcm(argv[3]),pcm(argv[4]),pcm(argv[5])};
  const auto known=pcm(argv[6]),unknown=pcm(argv[7]);
  auto result=object();
  auto before=parse(uid.identify(1,known));require(str(field(before.get(),"reason"))=="no_enrollments","test started with an enrolled speaker");
  put(result,"before",std::move(before));
  const auto prepared=uid.prepare_enrollment(current,policy,"237","Public evaluation speaker 237",selected);
  // The actual first-enrollment case: infer each final while the host's profile
  // is missing. The same retained embeddings—not caller-supplied vectors or a
  // second inference—must prepare exactly the same canonical whole batch.
  unavailable=true;
  for(size_t i=0;i<selected.size();++i) {
    bool missing=false;try{uid.identify(10+i,selected[i]);}catch(const EnrollmentUnavailable&){missing=true;}
    require(missing,"missing authoritative profile did not refuse identification");
  }
  unavailable=false;
  const auto began=std::chrono::steady_clock::now();
  const auto cached=uid.prepare_selected(current,policy,"237","Public evaluation speaker 237",{10,11,12});
  require(cached.snapshot==prepared.snapshot,"selected final custody changed prepared profile");
  const auto micros=std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-began).count();
  put(result,"selected_final_prepare_us",number(uint64_t(micros)));put(result,"selected_final_bytes_equal",boolean(true));
  bool foreign=false;try{uid.prepare_selected(current,policy,"237","Public evaluation speaker 237",{10,11,999});}catch(const std::invalid_argument&){foreign=true;}
  require(foreign,"foreign final selected");
  require(current==empty&&prepared.revision==1,"preparation published or revision differs");
  put(result,"prepared_snapshot",string(prepared.snapshot));put(result,"base_sha256",string(prepared.base_sha256));
  // Simulated host CAS in memory: this program has no publisher or file write.
  current=prepared.snapshot;
  auto after=parse(uid.identify(2,known)),other=parse(uid.identify(3,unknown));
  require(str(field(after.get(),"outcome"))=="known"&&str(field(after.get(),"speaker_id"))=="237","real selected voice not recognized after enrollment");
  require(str(field(other.get(),"outcome"))=="unknown","other voice became the enrolled speaker");
  put(result,"known",std::move(after));put(result,"unknown",std::move(other));
  bool duplicate=false;try{uid.prepare_enrollment(current,policy,"other","Other",selected);}catch(const std::invalid_argument&){duplicate=true;}
  require(duplicate,"real audio was reused as a different person");
  auto invalid=selected;invalid[1].resize(31919);bool partial=false;
  try{uid.prepare_enrollment(empty,policy,"other","Other",invalid);}catch(const std::invalid_argument&){partial=true;}
  require(partial&&current==prepared.snapshot,"failed batch changed current enrollment");
  const auto removed=aii::uid::prepare_removal(current,policy,"237");current=removed.snapshot;
  auto gone=parse(uid.identify(4,known));require(str(field(gone.get(),"reason"))=="no_enrollments","removed speaker still recognized");put(result,"removed",std::move(gone));
  uid.cancel();bool cancelled=false;try{uid.prepare_enrollment(current,policy,"237","Public evaluation speaker 237",selected);}catch(const Cancelled&){cancelled=true;}
  require(cancelled,"cancelled enrollment still prepared");
  uid.open();bool retired=false;try{uid.prepare_selected(current,policy,"237","Public evaluation speaker 237",{10,11,12});}catch(const std::invalid_argument&){retired=true;}
  require(retired,"new session reused earlier selected recordings");
  put(result,"passed",boolean(true));put(result,"published",boolean(false));put(result,"used_for_permissions",boolean(false));
  std::cout<<encode(result)<<'\n';
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
