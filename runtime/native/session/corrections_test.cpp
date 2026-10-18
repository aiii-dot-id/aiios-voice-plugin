#include "corrections_wire.h"
#include <cstdio>
#include <fstream>
#include <iostream>
#include <sstream>
using aii::voice::Corrections;
using aii::voice::Correction;
namespace {
void require(bool ok,const char* why) { if(!ok) throw std::runtime_error(why); }
bool refused(const Correction& rule) {
  try { Corrections::validate(rule); } catch(const std::invalid_argument&) { return true; }
  return false;
}
std::string utf8(uint32_t c) {
  std::string s;
  if(c<0x80) s+=char(c);
  else if(c<0x800) { s+=char(0xc0|(c>>6)); s+=char(0x80|(c&0x3f)); }
  else if(c<0x10000) { s+=char(0xe0|(c>>12)); s+=char(0x80|((c>>6)&0x3f)); s+=char(0x80|(c&0x3f)); }
  else { s+=char(0xf0|(c>>18)); s+=char(0x80|((c>>12)&0x3f)); s+=char(0x80|((c>>6)&0x3f)); s+=char(0x80|(c&0x3f)); }
  return s;
}
void vectors(const char* path) {
  using namespace aii::voice::wire;
  std::ifstream in(path,std::ios::binary); std::stringstream bytes; bytes<<in.rdbuf();
  require(in.good()||in.eof(),"vector file unreadable");
  auto j=parse(bytes.str());
  auto rule=[](const cJSON* row){ return Correction{bounded_text(field(row,"heard"),4096),bounded_text(field(row,"meant"),4096)}; };
  size_t valid=0,invalid=0,applied=0,listed=0,malformed=0;
  for(const auto* row=field(j.get(),"valid")->child;row;row=row->next,++valid) require(!refused(rule(row)),"a vector's valid rule was refused");
  for(const auto* row=field(j.get(),"invalid")->child;row;row=row->next,++invalid) require(refused(rule(row)),"a vector's invalid rule was accepted");
  for(const auto* row=field(j.get(),"apply")->child;row;row=row->next,++applied) {
    Corrections list;
    for(const auto* r=field(row,"rules")->child;r;r=r->next) list.correct(rule(r));
    const auto out=list.apply(str(field(row,"text"),4096));
    require(out.text==str(field(row,"corrected"),4096) && out.applied==integer(field(row,"applied")),"a vector's corrected text differs");
  }
  require(cJSON_IsArray(field(j.get(),"refused_code_points")) && cJSON_IsArray(field(j.get(),"malformed_utf8_hex")) &&
          cJSON_IsArray(field(j.get(),"joiners")) && cJSON_IsArray(field(j.get(),"spaces")),"vector file is incomplete");
  std::vector<bool> out(0x110000,false),space(0x110000,false),joins(0x110000,false); size_t spaces=0,joiners=0;
  for(const auto* row=field(j.get(),"refused_code_points")->child;row;row=row->next)
    for(auto c=std::stoul(str(field(row,"first")),nullptr,16),last=std::stoul(str(field(row,"last")),nullptr,16);c<=last;++c,++listed) out.at(c)=true;
  for(const auto* row=field(j.get(),"spaces")->child;row;row=row->next)
    for(auto c=std::stoul(str(field(row,"first")),nullptr,16),last=std::stoul(str(field(row,"last")),nullptr,16);c<=last;++c,++spaces) space.at(c)=true;
  for(const auto* row=field(j.get(),"joiners")->child;row;row=row->next,++joiners) joins.at(std::stoul(str(row),nullptr,16))=true;
  require(spaces==17 && joiners==2 && joins[0x200c] && joins[0x200d],"vector file is incomplete");
  const std::string joiner="\xe2\x80\x8d";
  for(uint32_t c=0;c<0x110000;++c) {
    if(c>=0xd800 && c<=0xdfff) continue;
    const bool word=c>=0x80 || c==' ' || c=='\'' || (c>='0'&&c<='9') || (c>='A'&&c<='Z') || (c>='a'&&c<='z');
    const auto text=utf8(c);
    const bool meant=refused({"a","b"+text+"c"}),heard=refused({"a"+text+"b","c"}),beside=refused({"a",text+joiner+text});
    if(meant==(out[c] && !joins[c]) && heard==(out[c] || !word) && beside==(out[c] || space[c])) continue;
    char why[160]; std::snprintf(why,sizeof why,"U+%04X: refused in meant %d, in heard %d, on both sides of a joiner %d; the vectors refuse it %d",unsigned(c),int(meant),int(heard),int(beside),int(out[c]));
    throw std::runtime_error(why);
  }
  for(uint32_t one=0x200c;one<=0x200d;++one) {
    const auto j1=utf8(one);
    for(const auto& unjoined:{j1+"bc","bc"+j1,j1,"b "+j1+"c","b"+j1+" c"})
      require(refused({"a",unjoined}),"a joiner outside a word was taken in what was meant");
    for(uint32_t c=0;c<0x110000;++c) if(space[c])
      require(refused({"a","b"+utf8(c)+j1+"c"}) && refused({"a","b"+j1+utf8(c)+"c"}) && !refused({"a","b"+utf8(c)+"c"+j1+"d"}),"a joiner beside a space was taken in what was meant");
    for(uint32_t two=0x200c;two<=0x200d;++two)
      require(refused({"a","b"+j1+utf8(two)+"c"}) && !refused({"a","b"+j1+"c"+utf8(two)+"d"}),"two joiners together were taken in what was meant");
  }
  for(const auto* row=field(j.get(),"malformed_utf8_hex")->child;row;row=row->next,++malformed) {
    const auto hex=str(field(row,"utf8_hex"));
    std::string raw;
    for(size_t i=0;i+1<hex.size();i+=2) raw+=char(std::stoul(hex.substr(i,2),nullptr,16));
    for(const auto& text:{raw,"b"+raw,"b"+raw+"c"})
      require(refused({"a",text}) && refused({text,"c"}),"a vector's malformed UTF-8 was held");
  }
  require(valid>=33 && invalid>=67 && applied>=6 && listed==237 && malformed>=14,"vector file is incomplete");
}
}
int main(int argc,char** argv) {
  try {
    if(argc==2) vectors(argv[1]);
    Corrections list;
    require(list.apply("Kwin said hello.").text=="Kwin said hello." && list.apply("").text.empty(),"an empty list changed text");
    require(list.correct({"Kwin","Quinn"}) && list.correct({"mapel","Maple"}) && list.correct({"say of","Quinn"}),"rules were not taken");
    require(!list.correct({"Kwin","Quinn"}),"an unchanged rule reported a change");

    auto r=list.apply("Kwin, tell Mapel that KWIN's notes are done. Kwintet and maplenna stay.");
    require(r.text=="Quinn, tell Maple that Quinn's notes are done. Kwintet and maplenna stay." && r.applied==3,"whole-word correction differs");
    r=list.apply("That is Mapel\xe2\x80\x99s idea");
    require(r.text=="That is Maple\xe2\x80\x99s idea" && r.applied==1,"typographic possessive lost");
    r=list.apply("I say of course. Say, of all people. say of");
    require(r.text=="I Quinn course. Say, of all people. Quinn" && r.applied==2,"phrase matching differs");
    require(list.correct({"Quinn","Seven"}),"second-stage rule not taken");
    r=list.apply("Kwin and Quinn");
    require(r.text=="Quinn and Seven" && r.applied==2,"rules chained");
    require(list.forget("QUINN") && !list.forget("quinn"),"forget is by heard phrase without case");
    require(list.correct({"maple","Maple"}),"capitalization rule not taken");
    r=list.apply("Maple and maple and MAPLE.");
    require(r.text=="Maple and Maple and Maple." && r.applied==2,"already-correct text was counted");
    r=list.apply("  \xc3\xa9t\xc3\xa9 \xe2\x80\x94 Mapel\t!  ");
    require(r.text=="  \xc3\xa9t\xc3\xa9 \xe2\x80\x94 Maple\t!  " && r.applied==1,"bytes outside a match changed");
    require(list.correct({"MAPEL","Mapel Quixote"}) && list.rules().size()==4,"re-teaching added a second rule");
    require(list.apply("mapel").text=="Mapel Quixote","re-taught rule not in force");

    for(const Correction& bad:{Correction{"",""},Correction{"a","a"},Correction{"a",""},Correction{" a","b"},Correction{"a  b","c"},
                               Correction{"a b c d e","f"},Correction{"a,b","c"},Correction{"a","b\nc"},Correction{"a"," b"},
                               Correction{"'","b"},Correction{std::string(65,'a'),"b"},Correction{"a",std::string(65,'b')},
                               Correction{"a","\xff"},Correction{"\xc3","b"}})
      require(refused(bad),"an invalid rule was accepted");
    for(const Correction& bad:{Correction{"a","b\xc2\x85" "c"},Correction{"a","b\xe2\x80\xa8" "c"},Correction{"a","b\xe2\x80\xa9"},
                               Correction{"a","\xe2\x80\xae" "b"},Correction{"a","b\xe2\x80\x8b" "c"},Correction{"a","b\xf3\xa0\x80\x81" "c"},
                               Correction{"a\xc2\x85" "b","c"},Correction{"a\xe2\x80\xa8" "b","c"},Correction{"a\xe2\x80\xa9","c"},
                               Correction{"\xe2\x80\xae" "a","c"},Correction{"a\xe2\x80\x8b" "b","c"},Correction{"a\xf3\xa0\x80\x81" "b","c"},
                               Correction{"a","b\xe0\x80\x8a" "c"},Correction{"a","\xed\xa0\x80"},Correction{"a","\xf4\x90\x80\x80"},
                               Correction{"a\xe0\x80\x8a" "b","c"},Correction{"\xed\xa0\x80","c"},Correction{"\xf4\x90\x80\x80","c"}})
      require(refused(bad),"a rule an operator cannot read as written was accepted");
    for(const Correction& good:{Correction{"cafe","caf\xc3\xa9"},Correction{"cafe\xcc\x81","coffee"},Correction{"tokyo","\xe6\x9d\xb1\xe4\xba\xac"},
                                Correction{"\xe6\x9d\xb1\xe4\xba\xac","Tokyo"},Correction{"salam","\xd8\xb3\xd9\x84\xd8\xa7\xd9\x85"},
                                Correction{"\xd7\xa9\xd7\x9c\xd7\x95\xd7\x9d","shalom"},Correction{"red heart","\xe2\x9d\xa4\xef\xb8\x8f"},
                                Correction{"wave","\xf0\x9f\x91\x8b\xf0\x9f\x8f\xbd"},Correction{"a","b\xc2\xa0" "c"}})
      require(!refused(good),"ordinary text in another script was refused");
    auto why=[](const Correction& rule) { try { Corrections::validate(rule); } catch(const std::invalid_argument& e) { return std::string(e.what()); } return std::string(); };
    require(why({"Kw\xe2\x80\x8b" "in","Quinn"})=="heard must not hold format characters, such as zero-width and direction marks" &&
            why({"Kwin","Quinn\xe2\x80\xae"})=="meant must not hold format characters, such as zero-width and direction marks" &&
            why({"a\xe2\x80\xa8" "b","c"})=="heard is words of letters, digits and apostrophes" &&
            why({"a","b\xe2\x80\xa8" "c"})=="meant must be one line of text" && why({"a","b\xe0\x80\x8a"})=="text must be valid UTF-8",
            "a refusal's words differ from the carrier's");
    const std::string persian="\xd9\x85\xdb\x8c\xe2\x80\x8c\xd8\xae\xd9\x88\xd8\xa7\xd9\x87\xd9\x85";
    for(const Correction& good:{Correction{"mikhaham",persian},Correction{"shri","\xe0\xb7\x81\xe0\xb7\x8a\xe2\x80\x8d\xe0\xb6\xbb\xe0\xb7\x93"},Correction{"family","\xf0\x9f\x91\xa8\xe2\x80\x8d\xf0\x9f\x91\xa9\xe2\x80\x8d\xf0\x9f\x91\xa7"},
                                Correction{"a","b" "\xe2\x80\x8c" "c d" "\xe2\x80\x8d" "e"}})
      require(!refused(good),"a joiner inside a word was refused in what was meant");
    for(const Correction& bad:{Correction{"a","\xe2\x80\x8d" "bc"},Correction{"a","bc" "\xe2\x80\x8c"},Correction{"a","\xe2\x80\x8d"},Correction{"a","b " "\xe2\x80\x8d" "c"},
                               Correction{"a","b" "\xe2\x80\x8c" " c"},Correction{"a","b" "\xe2\x80\x8d" "\xe2\x80\x8c" "c"},Correction{"a","b\xc2\xa0" "\xe2\x80\x8d" "c"},
                               Correction{"a" "\xe2\x80\x8c" "b","c"},Correction{"a" "\xe2\x80\x8d" "b","c"},Correction{persian,"mikhaham"}})
      require(refused(bad),"a joiner outside a word, or one in what was heard, was accepted");
    require(why({"a","bc" "\xe2\x80\x8d"})=="meant holds a joiner outside a word; a joiner is taken only inside a word" && why({"a","b " "\xe2\x80\x8c" "c"})=="meant holds a joiner outside a word; a joiner is taken only inside a word" &&
            why({"a" "\xe2\x80\x8c" "b","c"})=="heard must not hold format characters, such as zero-width and direction marks" && why({"a","b\xe2\x80\x8b" "c"})=="meant must not hold format characters, such as zero-width and direction marks",
            "a joiner's refusal differs from the carrier's");
    Corrections joined;
    require(joined.correct({"mikhaham",persian}),"a rule that writes a joined word was not taken");
    r=joined.apply("She said mikhaham, and again: Mikhaham.");
    require(r.text=="She said "+persian+", and again: "+persian+"." && r.applied==2,"a joined word was not written as it was taught");
    const auto times=[](const char* text,size_t n) { std::string s; while(n--) s+=text; return s; };
    for(const Correction& good:{Correction{"a",times("\xe6\x9d\xb1",22)},Correction{"a",times("\xe6\x9d\xb1",64)},Correction{"a",times("\xf0\x9f\x98\x80",64)},
                                Correction{times("\xf0\x9f\x98\x80",64),"b"},Correction{times("a",64),times("b",64)}})
      require(!refused(good),"a side of at most 64 characters was refused");
    for(const Correction& bad:{Correction{"a",times("\xe6\x9d\xb1",65)},Correction{times("\xe6\x9d\xb1",65),"b"},Correction{"a",times("\xf0\x9f\x98\x80",65)},
                               Correction{times("a",65),"b"},Correction{"a",times("b",65)}})
      require(refused(bad),"a side of 65 characters was accepted");
    static_assert(Corrections::max_chars==64 && Corrections::max_bytes==256 && Corrections::max_term_bytes==64,"a side is 64 characters, so 256 bytes; a term is 64 bytes");
    require(why({"a",times("\xe6\x9d\xb1",65)})=="meant must be 1..64 characters" && why({times("\xe6\x9d\xb1",65),"b"})=="heard must be 1..64 characters" &&
            why({"a",std::string(257,'\xff')})=="meant must be 1..64 characters" && why({std::string(257,'\xff'),"b"})=="heard must be 1..64 characters" &&
            why({"a",std::string(256,'\xff')})=="text must be valid UTF-8" && why({std::string(256,'\xff'),"b"})=="text must be valid UTF-8",
            "a side's length is not said as its length");
    Corrections full;
    for(int i=0;i<64;++i) full.correct({"w"+std::to_string(i),"x"});
    bool overflow=false; try { full.correct({"one more","x"}); } catch(const std::invalid_argument&) { overflow=true; }
    require(overflow && full.rules().size()==64,"the list exceeded its bound");
    require(full.correct({"W0","y"}) && full.rules().size()==64,"a full list refused to re-teach a held phrase");

    Corrections loaded; loaded.replace({{"Kwin","Quinn"},{"mapel","Maple"}});
    require(loaded.apply("kwin mapel").text=="Quinn Maple","a loaded list is not in force");
    bool twice=false; try { loaded.replace({{"Kwin","Quinn"},{"kwin","Kwen"}}); } catch(const std::invalid_argument&) { twice=true; }
    require(twice && loaded.rules().size()==2,"a document listing a phrase twice replaced the list");
    {
      using aii::voice::wire::parse;using aii::voice::wire::read_corrections;using aii::voice::wire::Refused;
      auto good=parse(R"({"schema":"aiii.voice.corrections","revision":7,"rules":[{"heard":"Kwin","meant":"Quinn"},{"heard":"mapel","meant":"Maple"}]})");
      const auto document=read_corrections(good.get());
      require(document.revision==7 && document.list.rules().size()==2 && document.list.apply("kwin").text=="Quinn","a stored list did not load");
      auto empty=parse(R"({"schema":"aiii.voice.corrections","revision":0,"rules":[]})");
      require(read_corrections(empty.get()).list.rules().empty(),"an empty stored list did not load");
      for(const char* bad:{R"([])",R"({"schema":"other","revision":1,"rules":[]})",R"({"schema":"aiii.voice.corrections","rules":[]})",
                           R"({"schema":"aiii.voice.corrections","revision":1,"rules":[],"extra":1})",R"({"schema":"aiii.voice.corrections","revision":-1,"rules":[]})",
                           R"({"schema":"aiii.voice.corrections","revision":1,"rules":{}})",R"({"schema":"aiii.voice.corrections","revision":1,"rules":[{"heard":"a"}]})",
                           R"({"schema":"aiii.voice.corrections","revision":1,"rules":[{"heard":"a","meant":"b","x":1}]})",
                           R"({"schema":"aiii.voice.corrections","revision":1,"rules":[{"heard":"a,b","meant":"c"}]})",
                           R"({"schema":"aiii.voice.corrections","revision":1,"rules":[{"heard":"a","meant":"b"},{"heard":"A","meant":"c"}]})"}) {
        bool no=false; try { auto j=parse(bad); read_corrections(j.get()); } catch(const Refused&) { no=true; }
        require(no,"an unreadable stored list was accepted");
      }
      for(const std::string& hidden:{std::string("\"heard\":\"a\",\"meant\":\"b\xe2\x80\xae" "c\""),std::string("\"heard\":\"a\xe2\x80\x8b" "b\",\"meant\":\"c\"")}) {
        bool no=false;
        try { auto j=parse(R"({"schema":"aiii.voice.corrections","revision":1,"rules":[{"heard":"Kwin","meant":"Quinn"},{)"+hidden+"}]}"); read_corrections(j.get()); }
        catch(const Refused&) { no=true; }
        require(no,"a stored list holding a rule that cannot be read as written was accepted");
      }
    }
    std::cout<<"corrections: whole words, no chaining, bounded, replaceable, stored form read whole or refused\n";
  } catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
