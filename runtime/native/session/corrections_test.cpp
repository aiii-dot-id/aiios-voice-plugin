#include "corrections_wire.h"
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
// spec/correction_vectors.json: the same file the carrier's validator is held to.
void vectors(const char* path) {
  using namespace aii::voice::wire;
  std::ifstream in(path,std::ios::binary); std::stringstream bytes; bytes<<in.rdbuf();
  require(in.good()||in.eof(),"vector file unreadable");
  auto j=parse(bytes.str());
  auto rule=[](const cJSON* row){ return Correction{bounded_text(field(row,"heard"),4096),bounded_text(field(row,"meant"),4096)}; };
  size_t valid=0,invalid=0,applied=0;
  for(const auto* row=field(j.get(),"valid")->child;row;row=row->next,++valid) require(!refused(rule(row)),"a vector's valid rule was refused");
  for(const auto* row=field(j.get(),"invalid")->child;row;row=row->next,++invalid) require(refused(rule(row)),"a vector's invalid rule was accepted");
  for(const auto* row=field(j.get(),"apply")->child;row;row=row->next,++applied) {
    Corrections list;
    for(const auto* r=field(row,"rules")->child;r;r=r->next) list.correct(rule(r));
    const auto out=list.apply(str(field(row,"text"),4096));
    require(out.text==str(field(row,"corrected"),4096) && out.applied==integer(field(row,"applied")),"a vector's corrected text differs");
  }
  require(valid>=8 && invalid>=18 && applied>=6,"vector file is incomplete");
}
}
int main(int argc,char** argv) {
  try {
    if(argc==2) vectors(argv[1]);
    Corrections list;
    require(list.apply("Kwin said hello.").text=="Kwin said hello." && list.apply("").text.empty(),"an empty list changed text");
    require(list.correct({"Kwin","Quinn"}) && list.correct({"rowen","Rowan"}) && list.correct({"say of","Quinn"}),"rules were not taken");
    require(!list.correct({"Kwin","Quinn"}),"an unchanged rule reported a change");

    // Whole words, without case, never inside a longer word, punctuation kept.
    auto r=list.apply("Kwin, tell Rowen that KWIN's notes are done. Kwintet and rowenna stay.");
    require(r.text=="Quinn, tell Rowan that Quinn's notes are done. Kwintet and rowenna stay." && r.applied==3,"whole-word correction differs");
    // A typographic possessive is kept as written.
    r=list.apply("That is Rowen\xe2\x80\x99s idea");
    require(r.text=="That is Rowan\xe2\x80\x99s idea" && r.applied==1,"typographic possessive lost");
    // The longer phrase wins at a position; a phrase does not cross punctuation.
    r=list.apply("I say of course. Say, of all people. say of");
    require(r.text=="I Quinn course. Say, of all people. Quinn" && r.applied==2,"phrase matching differs");
    // What a rule wrote is not read again.
    require(list.correct({"Quinn","Seven"}),"second-stage rule not taken");
    r=list.apply("Kwin and Quinn");
    require(r.text=="Quinn and Seven" && r.applied==2,"rules chained");
    require(list.forget("QUINN") && !list.forget("quinn"),"forget is by heard phrase without case");
    // Text already written as meant is neither changed nor counted.
    require(list.correct({"rowan","Rowan"}),"capitalization rule not taken");
    r=list.apply("Rowan and rowan and ROWAN.");
    require(r.text=="Rowan and Rowan and Rowan." && r.applied==2,"already-correct text was counted");
    // Bytes outside every match are untouched, including other scripts.
    r=list.apply("  \xc3\xa9t\xc3\xa9 \xe2\x80\x94 Rowen\t!  ");
    require(r.text=="  \xc3\xa9t\xc3\xa9 \xe2\x80\x94 Rowan\t!  " && r.applied==1,"bytes outside a match changed");
    // Teaching a phrase again replaces what it meant; one rule per phrase.
    require(list.correct({"ROWEN","Rowen Quixote"}) && list.rules().size()==4,"re-teaching added a second rule");
    require(list.apply("rowen").text=="Rowen Quixote","re-taught rule not in force");

    for(const Correction& bad:{Correction{"",""},Correction{"a","a"},Correction{"a",""},Correction{" a","b"},Correction{"a  b","c"},
                               Correction{"a b c d e","f"},Correction{"a,b","c"},Correction{"a","b\nc"},Correction{"a"," b"},
                               Correction{"'","b"},Correction{std::string(65,'a'),"b"},Correction{"a",std::string(65,'b')},
                               Correction{"a","\xff"},Correction{"\xc3","b"}})
      require(refused(bad),"an invalid rule was accepted");
    Corrections full;
    for(int i=0;i<64;++i) full.correct({"w"+std::to_string(i),"x"});
    bool overflow=false; try { full.correct({"one more","x"}); } catch(const std::invalid_argument&) { overflow=true; }
    require(overflow && full.rules().size()==64,"the list exceeded its bound");
    require(full.correct({"W0","y"}) && full.rules().size()==64,"a full list refused to re-teach a held phrase");

    Corrections loaded; loaded.replace({{"Kwin","Quinn"},{"rowen","Rowan"}});
    require(loaded.apply("kwin rowen").text=="Quinn Rowan","a loaded list is not in force");
    bool twice=false; try { loaded.replace({{"Kwin","Quinn"},{"kwin","Kwen"}}); } catch(const std::invalid_argument&) { twice=true; }
    require(twice && loaded.rules().size()==2,"a document listing a phrase twice replaced the list");
    {
      using aii::voice::wire::parse;using aii::voice::wire::read_corrections;using aii::voice::wire::Refused;
      auto good=parse(R"({"schema":"aiii.voice.corrections","revision":7,"rules":[{"heard":"Kwin","meant":"Quinn"},{"heard":"rowen","meant":"Rowan"}]})");
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
    }
    std::cout<<"corrections: whole words, no chaining, bounded, replaceable, stored form read whole or refused\n";
  } catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
