#include "snapshot.h"
#include "../native/session/worker_json.h"
#include "../native/vendor/picosha2/picosha2.h"
#include <charconv>
#include <cstring>
#include <regex>

namespace aii::uid {
namespace {
using namespace aii::voice::wire;
Json document(const std::string& s,size_t maximum) {
  require(!s.empty()&&s.size()<=maximum&&s.find('\0')==std::string::npos,"snapshot/policy byte bound");
  const char* end=nullptr;auto* p=cJSON_ParseWithLengthOpts(s.c_str(),s.size()+1,&end,1);
  require(p,"invalid snapshot JSON");auto j=own(p);size_t count=0;aii::voice::wire::validate(j.get(),0,count);return j;
}
void keys(const cJSON* p,std::initializer_list<const char*> expected) {
  require(cJSON_IsObject(p),"snapshot object required");size_t n=0;
  for(auto* child=p->child;child;child=child->next)++n;
  require(n==expected.size(),"snapshot fields differ");
  for(const auto* name:expected)require(field(p,name),"snapshot field missing");
}
std::string json_quote(const std::string& s) {
  std::string out="\"";const char* hex="0123456789abcdef";
  auto escape=[&](uint32_t cp){out+="\\u";for(int n=12;n>=0;n-=4)out+=hex[(cp>>n)&15];};
  for(const auto cp:unicode_scalars(s)) {
    require(cp>=32,"control character in enrollment label");
    if(cp=='"'||cp=='\\'){out+='\\';out+=char(cp);}
    else if(cp<127)out+=char(cp);
    else if(cp<=65535)escape(cp);
    else {const auto n=cp-65536;escape(0xd800+(n>>10));escape(0xdc00+(n&1023));}
  }
  return out+'"';
}
std::string lexeme(const std::string& raw,const char* key) {
  const auto marker="\""+std::string(key)+"\"";auto pos=raw.find(marker);
  require(pos!=std::string::npos&&raw.find(marker,pos+marker.size())==std::string::npos,"policy key spelling/duplication differs");
  pos=raw.find(':',pos+marker.size());require(pos!=std::string::npos,"policy colon missing");
  pos=raw.find_first_not_of(" \t\r\n",pos+1);require(pos!=std::string::npos,"policy number missing");
  auto end=raw.find_first_of(",} \t\r\n",pos);const auto value=raw.substr(pos,end-pos);
  static const std::regex number("-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?");
  require(std::regex_match(value,number),"invalid JSON policy number");return value;
}
std::string py_number(double x,const std::string& token) {
  require(std::isfinite(x),"nonfinite policy number");
  if(token.find_first_of(".eE")==std::string::npos)return std::to_string(int64_t(x));
  if(x==0)return std::signbit(x)?"-0.0":"0.0";
  char data[128];const auto r=std::to_chars(data,data+sizeof data,std::abs(x),std::chars_format::scientific);
  require(r.ec==std::errc{},"policy float serialization failed");std::string scientific(data,r.ptr);
  const auto e=scientific.find('e');require(e!=std::string::npos,"scientific float required");
  const int exponent=std::stoi(scientific.substr(e+1));auto digits=scientific.substr(0,e);
  const auto dot=digits.find('.');if(dot!=std::string::npos)digits.erase(dot,1);
  std::string out=x<0?"-":"";
  // Match Python's canonical JSON float spelling: repr's shortest digits,
  // fixed form at exponents -4..15, explicit .0 for an integral float.
  if(exponent>=-4&&exponent<16) {
    const int point=exponent+1;
    if(point<=0)out+="0."+std::string(size_t(-point),'0')+digits;
    else if(size_t(point)>=digits.size())out+=digits+std::string(size_t(point)-digits.size(),'0')+".0";
    else out+=digits.substr(0,point)+"."+digits.substr(point);
  } else {
    out+=digits[0];if(digits.size()>1)out+="."+digits.substr(1);
    auto exp=std::to_string(std::abs(exponent));if(exp.size()<2)exp="0"+exp;
    out+="e"+std::string(exponent<0?"-":"+")+exp;
  }
  return out;
}
}
std::string encode_base64(std::string_view data) {
  const char* chars="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";std::string out;
  out.reserve((data.size()+2)/3*4);
  for(size_t i=0;i<data.size();i+=3) {
    const auto n=data.size()-i;uint32_t x=uint32_t(uint8_t(data[i]))<<16;
    if(n>1)x|=uint32_t(uint8_t(data[i+1]))<<8;if(n>2)x|=uint8_t(data[i+2]);
    out+=chars[(x>>18)&63];out+=chars[(x>>12)&63];out+=n>1?chars[(x>>6)&63]:'=';out+=n>2?chars[x&63]:'=';
  }return out;
}
std::string decode_base64(std::string_view s,size_t maximum) {
  using aii::voice::wire::require;
  require(s.size()%4==0&&s.size()/4<=((maximum+2)/3),"base64 extent differs");
  const std::string alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";std::string out;
  for(size_t i=0;i<s.size();i+=4) {
    uint32_t bits=0;unsigned padding=0;
    for(size_t j=0;j<4;++j) {
      if(s[i+j]=='='){require(i+4==s.size()&&j>=2,"base64 padding differs");++padding;bits<<=6;}
      else {const auto n=alphabet.find(s[i+j]);require(!padding&&n!=std::string::npos,"invalid base64 digit");bits=(bits<<6)|uint32_t(n);}
    }
    out+=char((bits>>16)&255);if(padding<2)out+=char((bits>>8)&255);if(!padding)out+=char(bits&255);
  }
  require(out.size()<=maximum&&encode_base64(out)==s,"noncanonical base64");return out;
}
PolicyDocument read_policy(const std::string& raw) {
  auto doc=document(raw,4096);const auto* j=doc.get();
  keys(j,{"calibration_sha256","embedding_binding","minimum_enrollment_samples","minimum_margin","threshold"});
  const auto t=lexeme(raw,"threshold"),m=lexeme(raw,"minimum_margin"),n=lexeme(raw,"minimum_enrollment_samples");
  require(n.find_first_of("-.eE")==std::string::npos,"enrollment minimum must be an integer");
  for(const auto* key:{"threshold","minimum_margin"})require(cJSON_IsNumber(field(j,key)),"policy number required");
  Policy p{str(field(j,"embedding_binding")),str(field(j,"calibration_sha256")),std::string(64,'0'),
           field(j,"threshold")->valuedouble,field(j,"minimum_margin")->valuedouble,unsigned(integer(field(j,"minimum_enrollment_samples"),8))};
  validate(Snapshot{p,0,{}},p);
  std::string canonical="{\"calibration_sha256\":"+json_quote(p.calibration_sha256)+",\"embedding_binding\":"+json_quote(p.embedding_binding)+
    ",\"minimum_enrollment_samples\":"+std::to_string(p.minimum_enrollment_samples)+",\"minimum_margin\":"+py_number(p.minimum_margin,m)+
    ",\"threshold\":"+py_number(p.threshold,t)+"}";
  p.fingerprint=picosha2::hash256_hex_string(canonical);return {p,canonical};
}
std::string encode_vector(const Vector& embedding) {
  std::string bytes;bytes.reserve(2048);
  for(double value:embedding){uint64_t bits;std::memcpy(&bits,&value,8);for(unsigned i=0;i<8;++i)bytes+=char((bits>>(8*i))&255);}
  return encode_base64(bytes);
}
Vector decode_vector(std::string_view encoded) {
  const auto bytes=decode_base64(encoded,2048);require(bytes.size()==2048,"snapshot embedding extent differs");
  Vector result{};
  for(size_t i=0;i<256;++i){uint64_t bits=0;for(unsigned j=0;j<8;++j)bits|=uint64_t(uint8_t(bytes[8*i+j]))<<(8*j);std::memcpy(&result[i],&bits,8);}
  return result;
}
std::string write_snapshot(const Snapshot& s,const PolicyDocument& expected) {
  validate(s,expected.policy);
  require(picosha2::hash256_hex_string(expected.canonical)==expected.policy.fingerprint,"canonical policy hash differs");
  std::string raw="{\"policy\":"+expected.canonical+",\"revision\":"+std::to_string(s.revision)+",\"speakers\":[";
  bool first=true;
  for(const auto& speaker:s.speakers) {
    if(!first)raw+=',';first=false;raw+="{\"id\":"+json_quote(speaker.id)+",\"label\":"+json_quote(speaker.label)+",\"samples\":[";
    bool sample_first=true;
    for(const auto& sample:speaker.samples) {
      if(!sample_first)raw+=',';sample_first=false;
      raw+="{\"audio_sha256\":"+json_quote(sample.audio_sha256)+",\"embedding_f64le_b64\":\""+encode_vector(sample.embedding)+"\"}";
    }raw+="]}";
  }return raw+"]}";
}
Snapshot read_snapshot(const std::string& raw,const PolicyDocument& expected) {
  auto doc=document(raw,8<<20);keys(doc.get(),{"policy","revision","speakers"});
  const auto prefix="{\"policy\":"+expected.canonical+",\"revision\":";
  require(raw.compare(0,prefix.size(),prefix)==0,"snapshot policy binding/canonical form differs");
  const auto comma=raw.find(',',prefix.size());require(comma!=std::string::npos,"snapshot revision missing");
  uint64_t revision=0;const auto r=std::from_chars(raw.data()+prefix.size(),raw.data()+comma,revision);
  require(r.ec==std::errc{}&&r.ptr==raw.data()+comma&&revision<=INT64_MAX,"snapshot revision differs");
  Snapshot s{expected.policy,revision,{}};const auto* speakers=field(doc.get(),"speakers");
  require(cJSON_IsArray(speakers)&&cJSON_GetArraySize(speakers)<=256,"snapshot speaker count differs");
  for(auto* sp=speakers->child;sp;sp=sp->next) {
    keys(sp,{"id","label","samples"});Speaker speaker{str(field(sp,"id"),96),str(field(sp,"label"),512),{}};
    const auto* samples=field(sp,"samples");require(cJSON_IsArray(samples)&&cJSON_GetArraySize(samples)<=8,"snapshot sample count differs");
    for(auto* sample=samples->child;sample;sample=sample->next) {
      keys(sample,{"audio_sha256","embedding_f64le_b64"});Sample item;item.audio_sha256=str(field(sample,"audio_sha256"),64);
      item.embedding=decode_vector(str(field(sample,"embedding_f64le_b64"),2732));
      speaker.samples.push_back(std::move(item));
    }s.speakers.push_back(std::move(speaker));
  }
  require(write_snapshot(s,expected)==raw,"snapshot is not canonical");return s;
}
}
