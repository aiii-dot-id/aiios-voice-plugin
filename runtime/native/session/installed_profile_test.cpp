#include "installed_profile.h"
#include <chrono>
#include <iostream>
using namespace aii::voice::wire;
int main() {
 try {
  namespace fs=std::filesystem;
  auto root=fs::temp_directory_path()/("aii-profile-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
  require(fs::create_directory(root),"fresh fixture required");
  root=fs::canonical(root); // macOS /var is an alias for /private/var.
  struct Cleanup {fs::path p;~Cleanup(){fs::remove_all(p);}} cleanup{root};
  fs::create_directories(root/"runtime/resources");fs::create_directories(root/"models/voice bank");
  fs::create_directories(root/"models/asr");
  for(auto name:{"mel","vad","endpoint","coefficients","config","uid"})std::ofstream(root/"models"/name)<<"data";
  std::ofstream(root/"runtime/resources/policy.json")<<"{}";
  const std::string good=R"({"schema":"aiii.voice.native-profile","backend":"cpu","models":{"asr":"asr","asr_mel":"mel","vad":"vad","endpoint":"endpoint","endpoint_coefficients":"coefficients","tts":"voice bank","tts_config":"config","uid":"uid"},"uid_policy":"resources/policy.json"})";
  auto write=[&](const std::string& s){std::ofstream(root/"runtime/native-profile.json",std::ios::trunc)<<s;};write(good);
  auto p=InstalledProfile::read(root/"runtime",root/"models");
  require(p.asr_execution.empty(),"legacy target execution default changed");
  require(p.previous_uid_policy.empty(),"undeclared prior policy invented");
  std::ofstream(root/"runtime/resources/previous.json")<<"{}";
  write(good.substr(0,good.size()-1)+",\"uid_previous_policy\":\"resources/previous.json\"}");
  auto upgraded=InstalledProfile::read(root/"runtime",root/"models");
  require(upgraded.arguments==p.arguments&&fs::equivalent(fs::u8path(upgraded.previous_uid_policy),root/"runtime/resources/previous.json"),
      "bound prior policy altered current model/policy arguments");
  for(auto execution:{R"({"provider":"cpu"})",R"({"provider":"directml","adapter":"high_performance"})",R"({"provider":"directml","adapter":1})"}) {
    write(good.substr(0,good.size()-1)+",\"asr_execution\":"+execution+"}");
    const auto configured=InstalledProfile::read(root/"runtime",root/"models");
    require(configured.asr_execution==execution && configured.arguments==p.arguments,
            "ASR selection was lost or changed unrelated model/voice arguments");
  }
  require(fs::equivalent(fs::u8path(p.arguments[6]),root/"models/voice bank")&&p.arguments[8]=="cpu"&&fs::equivalent(fs::u8path(p.arguments[9]),root/"models/uid"),"installed arguments differ");
  for(auto backend:{"vulkan","metal"}) {
    auto s=good;auto at=s.find("\"backend\":\"cpu\"");s.replace(at,15,"\"backend\":\""+std::string(backend)+"\"");write(s);
    require(InstalledProfile::read(root/"runtime",root/"models").arguments[8]==backend,"explicit accelerator profile not preserved");
  }
  auto refuse=[&](const std::string& s){write(s);bool failed=false;try{InstalledProfile::read(root/"runtime",root/"models");}catch(...){failed=true;}require(failed,"invalid installed profile admitted");};
  for(auto prior:{"../policy.json","resources/missing.json","resources","/policy.json"})
    refuse(good.substr(0,good.size()-1)+",\"uid_previous_policy\":\""+prior+"\"}");
  refuse(good.substr(0,good.size()-1)+",\"uid_previous_policy\":null}");
  for(auto raw:{"null","[]","true","\"cpu\""})refuse(good.substr(0,good.size()-1)+",\"asr_execution\":"+raw+"}");
  refuse(good.substr(0,good.size()-1)+",\"asr_execution\":{\"provider\":\"cpu\",\"provider\":\"cpu\"}}");
  refuse(good.substr(0,good.size()-1)+",\"asr_execution\":{\"provider\":\""+std::string(2049,'x')+"\"}}");
  {auto s=good;auto at=s.find("\"backend\":\"cpu\"");s.replace(at,15,"\"backend\":\"magic\"");refuse(s);}
  for(auto replacement:{"../uid","/uid","C:/uid","asr/../uid","uid/","./uid","missing","asr"}) {
    auto s=good;auto at=s.find("\"uid\":\"uid\"");s.replace(at,11,"\"uid\":\""+std::string(replacement)+"\"");refuse(s);
  }
  refuse(good.substr(0,good.size()-1)+",\"extra\":true}");
  refuse(good.substr(0,good.size()-1)+",\"backend\":\"cpu\"}");refuse(std::string(32769,' '));
  write(good);std::error_code ec;fs::create_symlink(root/"models/uid",root/"models/link",ec);
  if(!ec) {auto s=good;auto at=s.find("\"uid\":\"uid\"");s.replace(at,11,"\"uid\":\"link\"");refuse(s);}
  else std::cout<<"symlink creation unavailable; traversal/kind/size/duplicate tests still executed\n";
  std::cout<<"installed profile binds paths, refuses traversal/missing/kind/unknown/duplicate/oversize PASS\n";
 } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
