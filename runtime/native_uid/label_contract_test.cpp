#include "enrollment.h"
#include "../native/session/worker_json.h"
#include <fstream>
#include <iostream>
using namespace aii::uid;
using namespace aii::voice::wire;
int main(int argc,char** argv){try{
  require(argc==2,"label vector path required");std::ifstream input(argv[1]);require(bool(input),"vectors missing");
  std::string bytes((std::istreambuf_iterator<char>(input)),{});auto rows=parse(bytes);require(cJSON_IsArray(rows.get()),"vectors not an array");
  auto p=read_policy("{\"calibration_sha256\":\""+std::string(64,'a')+"\",\"embedding_binding\":\""+std::string(64,'b')+"\",\"minimum_enrollment_samples\":1,\"minimum_margin\":0.105,\"threshold\":0.56}");
  const auto blank=write_snapshot({p.policy,0,{}},p);Vector v{};v[0]=1;
  unsigned count=0;
  for(auto* row=rows->child;row;row=row->next){
    const auto* raw=field(row,"utf8_hex");require(cJSON_IsString(raw),"hex missing");std::string hex=raw->valuestring,label;
    require(hex.size()%2==0,"odd hex");for(size_t i=0;i<hex.size();i+=2)label+=char(std::stoul(hex.substr(i,2),nullptr,16));
    bool admitted=true;try{(void)prepare_enrollment(blank,p,"sam",label,{{std::string(64,'c'),v}},p.policy.embedding_binding);}
    catch(const std::invalid_argument&){admitted=false;}catch(const Refused&){admitted=false;}
    if(admitted!=flag(field(row,"accepted")))throw std::runtime_error("native label disagreement: "+str(field(row,"name")));
    ++count;
  }
  require(count==17,"missing label vectors");std::cout<<"17 native enrollment label vectors PASS\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
