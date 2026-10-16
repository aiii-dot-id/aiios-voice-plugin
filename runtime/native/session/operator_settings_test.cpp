#include "operator_settings.h"
#include <iostream>
using namespace aii::voice::wire;
int main(int argc,char** argv) {
  try {
    if(argc==2&&std::string(argv[1])=="describe") {std::cout<<encode(OperatorSettings::declarations())<<'\n';return 0;}
    auto decls=OperatorSettings::declarations(),declared=object();require(cJSON_GetArraySize(decls.get())==8,"settings declaration incomplete");
    size_t vad=0;for(auto* row=decls->child;row;row=row->next) {
      const auto key=str(field(row,"key"),32);
      require(str(field(row,"scope"))==(key.rfind("tts_",0)==0?"speaking":"hearing"),"compiled setting scope differs");
      str(field(row,"title"),64);str(field(row,"description"),256); // SDK byte bounds
      if(key=="turn_pause_ms"||key=="vad_threshold") {
        ++vad;require(str(field(row,"title")).find("VAD")!=std::string::npos&&
          str(field(row,"description"),1024).find("always enabled")!=std::string::npos,"VAD activation is hidden from the operator");
      }
    }require(vad==2,"VAD settings missing");
    for(auto* row=decls->child;row;row=row->next)put(declared,str(field(row,"key")).c_str(),clone(field(row,"default")));
    require(encode(OperatorSettings::read(declared.get()).effective())==encode(OperatorSettings{}.effective()),"declarations differ from executable defaults");
    auto empty=parse("{}");const auto d=OperatorSettings::read(empty.get());require(d.voice=="alba" && d.control.pause_ms==768,"default profile changed");
    auto selected=parse(R"({"tts_voice":"marius","tts_language":"en","stt_language":"en","turn_pause_ms":1600,"vad_threshold":0.65,"tts_temperature":0.2,"tts_seed":42})");
    const auto s=OperatorSettings::read(selected.get());const auto effective=s.effective();
    require(s.voice=="marius" && s.control.pause_ms==1600 && s.seed==42 && s.control.speech_threshold==.65f && s.temperature==.2f,"settings did not reach typed configuration");
    require(str(field(effective.get(),"tts_voice"))=="marius" && integer(field(effective.get(),"turn_pause_ms"))==1600 && integer(field(effective.get(),"tts_seed"))==42,"effective settings lost selection");
    for(const char* bad:{R"({"tts_voice":"../alba"})",R"({"tts_voice":"unknown"})",R"({"tts_language":"xx"})",R"({"tts_language":"EN"})",R"({"tts_language":"french"})",R"({"stt_language":"fr"})",R"({"stt_language":"auto"})",R"({"tts_seed":-1})",R"({"tts_seed":4294967296})",R"({"tts_seed":1.5})",R"({"turn_pause_ms":319})",R"({"turn_pause_ms":5001})",R"({"turn_pause_ms":true})",R"({"vad_threshold":0})",R"({"tts_temperature":1.01})",R"({"rate":2})",R"({"tts_voice":"alba","tts_voice":"marius"})"}) {
      bool refused=false;try{auto j=parse(bad);OperatorSettings::read(j.get());}catch(const Refused&){refused=true;}require(refused,"invalid setting accepted");
    }
    for(const char* name:native_voices) {auto j=object();put(j,"tts_voice",string(name));require(OperatorSettings::read(j.get()).voice==name,"supported voice absent");}
    for(const auto& language:aii::voice::speech_languages) {
      auto j=object();put(j,"tts_language",string(language.code));const auto chosen=OperatorSettings::read(j.get());
      require(chosen.tts_language==language.code && chosen.stt_language=="en" && str(field(chosen.effective().get(),"tts_language"))==language.code,"speaking language selection/readback changed");
    }
    {
      const cJSON* speaking=nullptr;const cJSON* hearing=nullptr;
      for(auto* row=decls->child;row;row=row->next){const auto key=str(field(row,"key"),32);if(key=="tts_language")speaking=row;if(key=="stt_language")hearing=row;}
      require(speaking&&hearing&&cJSON_GetArraySize(field(speaking,"values"))==int(aii::voice::speech_languages.size())&&cJSON_GetArraySize(field(hearing,"values"))==1,"language choices differ from the compiled models");
      for(const auto& language:aii::voice::speech_languages)require(str(field(field(speaking,"labels"),language.code),64)==language.label,"speaking language label absent");
    }
    {
      using aii::voice::speech_replacements;using aii::voice::replace_speech_characters;
      // The shape the model publisher writes: guillemet removed, a typographic
      // apostrophe made plain (a doubled quote is one quote), colon to comma.
      const std::string config="weights_path: x\nremove_semicolons: true\n# note\nreplace_characters:\n  '\xc2\xab': ''\n  '\xe2\x80\x99': ''''\n  ':': ','\n  '(': ''\n  '\xe2\x80\x98': \"'\"\n\nflow_lm:\n  dtype: float32\n";
      const auto table=speech_replacements(config);
      require(table.size()==5 && table[1].second=="'" && table[2].second=="," && table[4].first=="\xe2\x80\x98" && table[4].second=="'","replacement table differs from its configuration");
      require(replace_speech_characters(table,"\xc2\xab Oui : l\xe2\x80\x99\xc3\xa9t\xc3\xa9 (ici)")==" Oui , l'\xc3\xa9t\xc3\xa9 ici)","replacement changed bytes outside its table");
      require(speech_replacements("flow_lm:\n  dtype: float32\n").empty() && replace_speech_characters({},"a (b)")=="a (b)","a model without replacements changed its text");
      require(replace_speech_characters(table,"\xc2\xab(").empty(),"a segment of replaced characters only must become empty");
      for(const char* bad:{"replace_characters:\n  \"a\\n\": ''\n","replace_characters:\n  'a': \"b\n","replace_characters:\n  a: ''\n","replace_characters:\n  'a': b\n","replace_characters:\n    'a': ''\n","replace_characters:\n  'a': '' # x\n","replace_characters:\n  '': 'a'\n","replace_characters:\n  'a: ''\n"}) {
        bool refused=false;try{speech_replacements(bad);}catch(const std::invalid_argument&){refused=true;}require(refused,"unreadable replacement row accepted");
      }
    }
    require(d.capture_limit_minutes==30,"capture duration default changed");
    for(uint32_t minutes:{0u,1u,31u,UINT32_MAX}) {
      auto j=object();put(j,"capture_limit_minutes",number(minutes));
      const auto choice=OperatorSettings::read(j.get());
      require(choice.capture_limit_minutes==minutes && integer(field(choice.effective().get(),"capture_limit_minutes"))==minutes,"capture duration selection/readback changed");
    }
    for(const char* bad:{"-1","0.5","true","null","\"0\"","4294967296"}) {
      bool refused=false;
      try {auto j=parse(std::string("{\"capture_limit_minutes\":")+bad+"}");OperatorSettings::read(j.get());}
      catch(const Refused&){refused=true;}
      require(refused,"invalid capture duration accepted");
    }
    std::cout<<"typed voice/language/pause/VAD/sampling settings and truthful readback\n";
  } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
