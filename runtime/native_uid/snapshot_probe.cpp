#include "snapshot.h"
#include <fstream>
#include <iostream>
static std::string read(const char* path) {
  std::ifstream f(path,std::ios::binary);if(!f)throw std::runtime_error("input unavailable");
  std::string bytes;char block[4096];
  while(f.read(block,sizeof block)||f.gcount()) {
    bytes.append(block,size_t(f.gcount()));
    if(bytes.size()>(8u<<20))throw std::runtime_error("input bound");
  }
  if(!f.eof())throw std::runtime_error("input read failed");return bytes;
}
static void contracts() {
  using namespace aii::uid;
  const auto policy=read_policy("{\"calibration_sha256\":\""+std::string(64,'a')+"\",\"embedding_binding\":\""+std::string(64,'b')+
    "\",\"minimum_enrollment_samples\":3,\"minimum_margin\":0.105,\"threshold\":0.56}");
  Vector vector{};vector[0]=1;vector[1]=-0.;
  const std::string label="DEL\x7f "+std::string("\xf0\x9f\x98\x80");
  Snapshot s{policy.policy,9007199254740993ULL,{{"one",label,{{std::string(64,'c'),vector}}}}};
  auto raw=write_snapshot(s,policy);auto loaded=read_snapshot(raw,policy);
  if(loaded.revision!=s.revision||loaded.speakers[0].label!=label||raw.find("DEL\\u007f \\ud83d\\ude00")==std::string::npos||write_snapshot(loaded,policy)!=raw)
    throw std::runtime_error("canonical label/revision round trip changed");
  try{read_snapshot(raw+" ",policy);throw 42;}catch(const std::exception&){}catch(...){throw std::runtime_error("noncanonical snapshot accepted");}
  std::cout<<"canonical snapshot label, float policy, revision and invalid-input contracts PASS\n";
}
int main(int argc,char** argv) {
  try {
    if(argc==1){contracts();return 0;}
    if(argc!=2&&argc!=3)throw std::runtime_error("policy [snapshot] required");
    const auto policy=aii::uid::read_policy(read(argv[1]));
    if(argc==2)std::cout<<policy.canonical;
    else std::cout<<aii::uid::write_snapshot(aii::uid::read_snapshot(read(argv[2]),policy),policy);
    return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 2;}
}
