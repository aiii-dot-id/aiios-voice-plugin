#include "enrollment.h"
#include "../native/session/worker_json.h"
#include <cstdio>
#include <fstream>
#include <iostream>
using namespace aii::uid;
using namespace aii::voice::wire;
namespace {
// A code point as UTF-8, written here and not taken from the reader under test.
std::string utf8(uint32_t c) {
  std::string s;
  if(c<0x80)s+=char(c);
  else if(c<0x800){s+=char(0xc0|(c>>6));s+=char(0x80|(c&0x3f));}
  else if(c<0x10000){s+=char(0xe0|(c>>12));s+=char(0x80|((c>>6)&0x3f));s+=char(0x80|(c&0x3f));}
  else{s+=char(0xf0|(c>>18));s+=char(0x80|((c>>12)&0x3f));s+=char(0x80|((c>>6)&0x3f));s+=char(0x80|(c&0x3f));}
  return s;
}
uint32_t point(const cJSON* j){return uint32_t(std::stoul(str(j),nullptr,16));}
// One of the vectors' tables of code points, and how many it lists.
std::vector<bool> table(const cJSON* ranges,size_t& listed) {
  require(cJSON_IsArray(ranges),"label table missing");std::vector<bool> out(0x110000,false);listed=0;
  for(auto* row=ranges->child;row;row=row->next)
    for(auto c=point(cJSON_IsObject(row)?field(row,"first"):row),last=point(cJSON_IsObject(row)?field(row,"last"):row);c<=last;++c,++listed)out.at(c)=true;
  return out;
}
}
// spec/uid_label_vectors.json: the same file the carrier's rule and the
// operations' input schemas are held to (plugin/native/label_contract_test.go).
int main(int argc,char** argv){try{
  require(argc==2,"label vector path required");std::ifstream input(argv[1]);require(bool(input),"vectors missing");
  std::string bytes((std::istreambuf_iterator<char>(input)),{});auto vectors=parse(bytes);require(cJSON_IsObject(vectors.get()),"vectors not an object");
  auto p=read_policy("{\"calibration_sha256\":\""+std::string(64,'a')+"\",\"embedding_binding\":\""+std::string(64,'b')+"\",\"minimum_enrollment_samples\":1,\"minimum_margin\":0.105,\"threshold\":0.56}");
  const auto blank=write_snapshot({p.policy,0,{}},p);Vector v(256);v[0]=1;
  // What speaker.enroll does with a label it is given.
  const auto admitted=[&](const std::string& label){
    try{(void)prepare_enrollment(blank,p,"sam",label,{{std::string(64,'c'),v}},p.policy.embedding_binding);return true;}
    catch(const std::invalid_argument&){return false;}catch(const Refused&){return false;}
  };
  unsigned count=0;
  const auto* rows=field(vectors.get(),"labels");require(cJSON_IsArray(rows),"label rows missing");
  for(auto* row=rows->child;row;row=row->next){
    const auto* raw=field(row,"utf8_hex");require(cJSON_IsString(raw),"hex missing");std::string hex=raw->valuestring,label;
    require(hex.size()%2==0,"odd hex");for(size_t i=0;i<hex.size();i+=2)label+=char(std::stoul(hex.substr(i,2),nullptr,16));
    if(admitted(label)!=flag(field(row,"accepted")))throw std::runtime_error("native label disagreement: "+str(field(row,"name")));
    // The rule alone says nothing of length, and never takes what enrollment takes for less.
    if(flag(field(row,"accepted"))&&!readable_label(label))throw std::runtime_error("the label rule refuses a label enrollment takes: "+str(field(row,"name")));
    ++count;
  }
  require(count>=63,"missing label vectors");
  // Bytes that are not UTF-8 are no label, to the rule itself and not only to
  // the codec behind it: an overlong form, a surrogate, a sequence cut short,
  // a line feed in three bytes, a value beyond U+10FFFF, a lone continuation.
  for(const char* bad:{"\xc0\xaf","\xed\xa0\x80","\xf0\x9f\x8e","a\xe0\x80\x8a" "b","\xf4\x90\x80\x80","a\x80"})
    require(!readable_label(bad)&&!admitted(bad),"bytes that are not UTF-8 were taken for a label");
  // A label is a person's name as an operator reads and confirms it. Every
  // code point it may not hold is tried in every place it could stand, the
  // two joiners beside every space, and the rule itself on every code point
  // there is: the carrier and the schemas are asked the same and must agree.
  size_t refuses=0,blanks=0,joins=0;
  const auto refused=table(field(vectors.get(),"refused_code_points"),refuses),spaces=table(field(vectors.get(),"spaces"),blanks),
    joiners=table(field(vectors.get(),"joiners"),joins);
  require(refuses==237&&blanks==17&&joins==2&&joiners[0x200c]&&joiners[0x200d],"label tables incomplete");
  unsigned cases=0;
  const auto judge=[&](const std::string& label,bool want,uint32_t c,const char* where){
    ++cases;if(readable_label(label)==want&&admitted(label)==want)return;
    char why[128];std::snprintf(why,sizeof why,"U+%04X %s: a label was %s",unsigned(c),where,want?"refused":"taken");throw std::runtime_error(why);
  };
  const std::string joiner="\xe2\x80\x8d";
  for(uint32_t c=0;c<0x110000;++c){
    if(c>=0xd800&&c<=0xdfff)continue; // surrogates are not characters: no text holds one
    const auto text=utf8(c);
    // Alone between two letters: text, or one of the two joiners. On both
    // sides of a joiner: anything a label holds but a space.
    if(readable_label("a"+text+"b")!=(!refused[c]||joiners[c])||readable_label(text+joiner+text)!=(!refused[c]&&!spaces[c])){
      char why[96];std::snprintf(why,sizeof why,"U+%04X: the label rule and the vectors differ",unsigned(c));throw std::runtime_error(why);
    }
    if(spaces[c]){
      judge("a"+text+"b",true,c,"between two letters");
      for(uint32_t j:{0x200cu,0x200du}){judge("a"+text+utf8(j)+"b",false,c,"before a joiner");judge("a"+utf8(j)+text+"b",false,c,"after a joiner");}
    }
    if(!refused[c])continue;
    judge(text+"ab",false,c,"first");judge("ab"+text,false,c,"last");judge(text,false,c,"alone");
    judge("a"+text+text+"b",false,c,"doubled");judge("a "+text+"b",false,c,"after a space");judge("a"+text+" b",false,c,"before a space");
    judge("a"+text+"b",joiners[c],c,"between two letters");
    // The code point after a refused range is text again.
    if(c+1<0x110000&&!refused[c+1])judge("a"+utf8(c+1)+"b",true,c+1,"beside a refused range");
  }
  for(uint32_t one:{0x200cu,0x200du})for(uint32_t two:{0x200cu,0x200du}){
    judge("a"+utf8(one)+utf8(two)+"b",false,one,"beside another joiner");judge("a"+utf8(one)+"b"+utf8(two)+"c",true,one,"with a letter before the next joiner");
  }
  // A label already stored is not judged again: one holding what a new label
  // may not (here a right-to-left override) still reads, and still matches.
  Snapshot stored{p.policy,1,{{"sam","a\xe2\x80\xae" "b",{{std::string(64,'c'),v}}}}};
  require(read_snapshot(write_snapshot(stored,p),p).speakers[0].label=="a\xe2\x80\xae" "b","a stored label became unreadable");
  std::cout<<count<<" native enrollment label vectors and "<<cases<<" placed code points PASS\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
