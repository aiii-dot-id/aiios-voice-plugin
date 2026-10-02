#include "installed_profile.h"
#include "hearing_profile.h"
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
  const auto graphs=root/"models/asr";
  const std::string hearing=R"({"diarizer":"nemotron","gpu":0,"encoder_cuda":-1})";
  const std::string cpu_hearing=R"({"diarizer":"nemotron","gpu":-1,"encoder_cuda":-1})";
  auto hearing_refuses=[&](const std::string& raw){bool failed=false;try{HearingProfile::read(raw,graphs);}catch(...){failed=true;}require(failed,"invalid hearing profile admitted");};
  hearing_refuses(hearing); // Mandatory model is absent, not a legacy fallback.
  hearing_refuses(cpu_hearing); // CPU also requires the exact bound diarizer.
  std::ofstream(graphs/"nemotron.gguf")<<"fixture";
  const auto selected=HearingProfile::read(hearing,graphs);
  require(selected.gpu==0&&selected.encoder_cuda==-1&&fs::equivalent(selected.model,graphs/"nemotron.gguf"),"hearing binding changed");
  require(selected.encoder_threads==2,"existing hearing thread default changed");
  const auto cpu=HearingProfile::read(cpu_hearing,graphs);
  require(cpu.gpu==-1&&cpu.encoder_cuda==-1&&cpu.model==selected.model&&
      cpu.encoder_threads==selected.encoder_threads,"CPU hearing dropped or changed the diarizer binding");
  const auto hybrid=HearingProfile::read(R"({"diarizer":"nemotron","gpu":-1,"encoder_cuda":0})",graphs);
  require(hybrid.gpu==-1&&hybrid.encoder_cuda==0&&hybrid.model==selected.model,
      "diarizer placement changed independent recognition placement");
  auto separation=[&](const std::string& object) {
    return hearing.substr(0,hearing.size()-1)+",\"separator\":"+object+"}";
  };
  hearing_refuses(separation(R"({"backend":"onnx","threads":4,"cuda":-1})"));
  const std::string cpu_separation=cpu_hearing.substr(0,cpu_hearing.size()-1)+
      R"(,"separator":{"backend":"onnx","threads":4,"cuda":-1}})";
  hearing_refuses(cpu_separation);
  std::ofstream(graphs/"separator.onnx")<<"fixture";
  fs::create_directory(graphs/"separator-coreml");
  auto sep=HearingProfile::read(separation(R"({"backend":"onnx","threads":4,"cuda":-1})"),graphs);
  require(sep.separator_threads==4&&sep.separator_cuda==-1&&fs::equivalent(sep.separator_model,graphs/"separator.onnx"),"ONNX separation binding differs");
  const auto cpu_sep=HearingProfile::read(cpu_separation,graphs);
  require(cpu_sep.gpu==-1&&cpu_sep.encoder_cuda==-1&&cpu_sep.model==selected.model&&
      cpu_sep.separator_backend=="onnx"&&cpu_sep.separator_threads==4&&cpu_sep.separator_cuda==-1&&
      cpu_sep.separator_model==sep.separator_model,"CPU hearing lost overlap separation");
  sep=HearingProfile::read(separation(R"({"backend":"coreml"})"),graphs);
  require(sep.separator_backend=="coreml"&&fs::equivalent(sep.separator_model,graphs/"separator-coreml"),"Core ML separation binding differs");
  for(auto bad:{"null","true","[]",R"({"backend":"other"})",R"({"backend":"coreml","cuda":0})",
      R"({"backend":"onnx","threads":0,"cuda":-1})",R"({"backend":"onnx","threads":65,"cuda":-1})",
      R"({"backend":"onnx","threads":2,"cuda":-2})",R"({"backend":"onnx","threads":2,"cuda":64})",
      R"({"backend":"onnx","threads":2.5,"cuda":-1})",R"({"backend":"onnx","threads":true,"cuda":-1})",
      R"({"backend":"onnx","threads":2})",R"({"backend":"onnx","threads":2,"cuda":-1,"path":"other"})"})
    hearing_refuses(separation(bad));
  for(int count:{1,8,16}) {
    const auto tuned=HearingProfile::read(hearing.substr(0,hearing.size()-1)+
      ",\"encoder_threads\":"+std::to_string(count)+"}",graphs);
    require(tuned.encoder_threads==count&&tuned.gpu==selected.gpu&&
      tuned.encoder_cuda==selected.encoder_cuda&&tuned.model==selected.model,"hearing thread binding differs");
  }
  for(const auto* value:{"0","17","-1","1.5","true","null","\"8\""})
    hearing_refuses(hearing.substr(0,hearing.size()-1)+",\"encoder_threads\":"+value+"}");
  hearing_refuses(hearing.substr(0,hearing.size()-1)+",\"encoder_threads\":8,\"encoder_threads\":2}");
  require(HearingProfile::read("",graphs).model.empty(),"legacy hearing selection changed");
  for(auto raw:{R"({"diarizer":"sortformer","gpu":0,"encoder_cuda":-1})",
      R"({"diarizer":"nemotron","gpu":-2,"encoder_cuda":-1})",
      R"({"diarizer":"nemotron","gpu":64,"encoder_cuda":-1})",
      R"({"diarizer":"nemotron","gpu":true,"encoder_cuda":-1})",
      R"({"diarizer":"nemotron","gpu":null,"encoder_cuda":-1})",
      R"({"diarizer":"nemotron","gpu":"-1","encoder_cuda":-1})",
      R"({"diarizer":"nemotron","gpu":0.5,"encoder_cuda":-1})",
      R"({"diarizer":"nemotron","gpu":0,"encoder_cuda":64})",
      R"({"diarizer":"nemotron","gpu":0,"encoder_cuda":true})",
      R"({"diarizer":"nemotron","gpu":0,"encoder_cuda":-1,"model":"other"})",
      R"({"diarizer":"nemotron","gpu":0,"gpu":1,"encoder_cuda":-1})"})hearing_refuses(raw);
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
  write(good.substr(0,good.size()-1)+",\"asr_execution\":"+cpu_separation+"}");
  const auto cpu_installed=InstalledProfile::read(root/"runtime",root/"models");
  require(cpu_installed.arguments==p.arguments&&
      cpu_installed.asr_execution==cpu_separation,"CPU hearing changed common voice, UID, VAD or endpoint bindings");
  require(fs::equivalent(fs::u8path(p.arguments[6]),root/"models/voice bank")&&p.arguments[8]=="cpu"&&fs::equivalent(fs::u8path(p.arguments[9]),root/"models/uid"),"installed arguments differ");
#ifdef _WIN32
  // The installed model root is a wide Windows environment value. Every
  // derived model and policy path remains UTF-8 until opened as fs::path.
  const auto unicode=root/fs::u8path(u8"modèles");
  fs::create_directories(unicode/"asr");fs::create_directories(unicode/"voice bank");
  for(auto name:{"mel","vad","endpoint","coefficients","config","uid"})std::ofstream(unicode/name)<<"data";
  require(SetEnvironmentVariableW(L"AII_MODELS_DIR",unicode.c_str())!=0,"cannot set Unicode model root fixture");
  const auto from_env=InstalledProfile::host_model_root();
  require(fs::equivalent(from_env,unicode),"host model root was narrowed through the ANSI code page");
  const auto native=InstalledProfile::read(root/"runtime",from_env);
  for(size_t index:{2u,3u,4u,5u,7u,9u,10u}) {
    std::ifstream file(fs::u8path(native.arguments[index]),std::ios::binary);
    require(bool(file),"UTF-8 model or policy path could not be opened as a Windows path");
  }
#endif
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
