#include "execution_policy.h"
#include <iostream>
#include <stdexcept>
using namespace aii::asr;
void need(bool b,const char* why){if(!b)throw std::runtime_error(why);}
template<class F>void refuses(F f){bool caught=false;try{f();}catch(const std::exception&){caught=true;}need(caught,"invalid policy or unavailable GPU accepted");}
int main(){try{
  need(ExecutionPolicy::read(nullptr).target_default,"default target changed");
  auto cpu=ExecutionPolicy::read(R"({"provider":"cpu"})");
  need(!cpu.directml&&!cpu.target_default,"explicit CPU became target default");
  auto preferred=ExecutionPolicy::read(R"({"provider":"directml","adapter":"high_performance"})");
  auto exact=ExecutionPolicy::read(R"({"provider":"directml","adapter":1})");
  need(preferred.directml&&preferred.adapter==-1&&exact.adapter==1,"selector interpretation");
  // Preference order differs from EnumAdapters1 order; no index conflation.
  std::vector<AdapterCandidate> devices={{0,2,false,true},{1,0,false,true},{2,1,true,true}};
  need(select_adapter(preferred,devices)==1,"preferred GPU not selected");
  need(select_adapter(exact,devices)==1,"explicit device changed");
  devices[1].d3d12=false;
  need(select_adapter(preferred,devices)==0,"unsupported preferred device not excluded");
  refuses([&]{select_adapter(exact,devices);});
  devices[0].software=true;refuses([&]{select_adapter(preferred,devices);});
  refuses([&]{select_adapter(preferred,{});});refuses([&]{select_adapter(cpu,devices);});
  for(const char* raw:{"", "null", "[]", "{}", R"({"provider":"cuda"})",
      R"({"provider":"cpu","adapter":0})", R"({"provider":"directml"})",
      R"({"provider":"directml","adapter":true})", R"({"provider":"directml","adapter":-1})",
      R"({"provider":"directml","adapter":128})", R"({"provider":"directml","adapter":0.1})",
      R"({"provider":"directml","adapter":"fast"})", R"({"provider":"cpu","provider":"cpu"})",
      R"({"provider":"cpu\u0000hidden"})", R"({"provider":"cpu"} trailing)"})
    refuses([&]{ExecutionPolicy::read(raw);});
  const std::string huge(2049,' ');refuses([&]{ExecutionPolicy::read(huge.c_str());});
  std::cout<<"PASS: typed bounded policy, explicit CPU, preference/index mapping, unavailable GPU refusal\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
