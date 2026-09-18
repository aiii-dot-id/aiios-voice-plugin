#pragma once
#include "c_api.h"
#include "capture_limit.h"
#include "worker_json.h"
#include <array>
namespace aii::voice::wire {
// Values match the native preset filenames, not voice IDs from another model.
inline constexpr std::array<const char*,10> native_voices={"alba","marius","javert","fantine","eponine","azelma","bill_boerst","peter_yearsley","stuart_bell","caro_davy"};
inline constexpr std::array<const char*,10> native_voice_labels={"Alba - casual dialogue","Marius","Javert","Fantine","Éponine","Azelma","Bill Boerst - audiobook narrator","Peter Yearsley - audiobook narrator","Stuart Bell - audiobook narrator","Caro Davy - audiobook narrator"};
struct OperatorSettings {
  aii_voice_settings control{768,3000,.5f};
  std::string voice="alba",tts_language="en",stt_language="en";
  float temperature=.3f;
  uint32_t seed=20260908;
  uint32_t capture_limit_minutes=aii::voice::default_capture_limit_minutes;
  aii_voice_speech_settings speech() const {return {voice.c_str(),tts_language.c_str(),stt_language.c_str(),temperature,seed};}
  static double fraction(const cJSON* v,double low,double high) {
    require(cJSON_IsNumber(v) && std::isfinite(v->valuedouble) && v->valuedouble>=low && v->valuedouble<=high,"numeric setting outside supported range");return v->valuedouble;
  }
  static OperatorSettings read(const cJSON* values) {
    require(cJSON_IsObject(values),"settings object required");OperatorSettings result;
    for(auto* v=values->child;v;v=v->next) {
      const std::string key=v->string;
      if(key=="turn_pause_ms") {result.control.pause_ms=uint32_t(integer(v,5000));require(result.control.pause_ms>=320,"pause must be 320..5000 ms");}
      else if(key=="capture_limit_minutes")result.capture_limit_minutes=uint32_t(integer(v,UINT32_MAX));
      else if(key=="vad_threshold")result.control.speech_threshold=float(fraction(v,.05,.95));
      else if(key=="tts_temperature")result.temperature=float(fraction(v,0,1));
      else if(key=="tts_seed")result.seed=uint32_t(integer(v,4294967295ULL));
      else if(key=="tts_voice") {
        result.voice=str(v,64);bool present=false;for(const char* name:native_voices)present|=result.voice==name;
        require(present,"unsupported native voice preset");
      } else if(key=="tts_language" || key=="stt_language") {
        auto language=str(v,16);require(language=="en","this native model profile supports English only");
        (key=="tts_language"?result.tts_language:result.stt_language)=language;
      } else throw Refused("setting unsupported by this explicit native profile");
    }
    return result;
  }
  Json effective() const {
    auto j=object();put(j,"turn_pause_ms",number(control.pause_ms));
    put(j,"capture_limit_minutes",number(capture_limit_minutes));
    put(j,"vad_threshold",own(cJSON_CreateNumber(control.speech_threshold)));
    put(j,"tts_voice",string(voice));put(j,"tts_language",string(tts_language));put(j,"stt_language",string(stt_language));
    put(j,"tts_temperature",own(cJSON_CreateNumber(temperature)));put(j,"tts_seed",number(seed));return j;
  }
  static Json declarations() {
    auto out=own(cJSON_CreateArray());const auto defaults=OperatorSettings{}.effective();
    auto add=[&](const char* key,const char* type,const char* title,const char* description){
      auto row=object();put(row,"key",string(key));put(row,"type",string(type));put(row,"title",string(title));
      put(row,"description",string(description));put(row,"default",clone(field(defaults.get(),key)));return row;
    };
    auto voice=add("tts_voice","enum","Speaking voice","Fixed preset for every reply segment. Applies next session. Separate from speaker identity.");
    auto choices=own(cJSON_CreateArray()),labels=object();
    for(size_t i=0;i<native_voices.size();++i){cJSON_AddItemToArray(choices.get(),string(native_voices[i]).release());put(labels,native_voices[i],string(native_voice_labels[i]));}
    put(voice,"values",std::move(choices));put(voice,"labels",std::move(labels));cJSON_AddItemToArray(out.get(),voice.release());
    for(const char* key:{"tts_language","stt_language"}) {
      auto row=add(key,"enum",std::string(key)=="tts_language"?"Speaking language":"Recognition language","This compact native model profile supports English only. No automatic language fallback.");
      auto values=own(cJSON_CreateArray()),names=object();cJSON_AddItemToArray(values.get(),string("en").release());put(names,"en",string("English"));put(row,"values",std::move(values));put(row,"labels",std::move(names));cJSON_AddItemToArray(out.get(),row.release());
    }
    auto numeric=[&](const char* key,const char* type,const char* title,double low,double high,const char* description){
      auto row=add(key,type,title,description);put(row,"minimum",own(cJSON_CreateNumber(low)));put(row,"maximum",own(cJSON_CreateNumber(high)));cJSON_AddItemToArray(out.get(),row.release());
    };
    numeric("turn_pause_ms","integer","Speaking pause (VAD, ms)",320,5000,"Voice activity detection is always enabled. Minimum silence before turn completion; short pauses retain speech and semantic handling may extend it. Applies next session; does not delay interruption.");
    numeric("capture_limit_minutes","integer","Listening session limit (minutes; 0 = no automatic stop)",0,UINT32_MAX,"Captured audio minutes, including silence. 0 means no automatic stop. Positive limits finalize accepted input. Applies next session; separate from VAD pause and enrollment. Stop, Finish and Abort remain available.");
    numeric("vad_threshold","number","Speech detection (VAD) threshold",.05,.95,"Voice activity detection is always enabled. Higher values require stronger speech evidence. Applies next session; changing this can miss quiet speech.");
    numeric("tts_temperature","number","Voice variation",0,1,"Advanced sampling control. Applies next session; higher values may reduce consistency.");
    numeric("tts_seed","integer","Synthesis seed",0,4294967295.0,"Stable sampling seed. Applies next session. Does not enroll or identify a speaker.");
    return out;
  }
};
}
