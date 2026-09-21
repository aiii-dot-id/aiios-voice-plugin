#include "attribution.h"
#include <functional>
#include <fstream>
#include <iostream>
#include <sstream>
using namespace aii::voice::wire;
using aii::voice::speaker_queue_capacity;
void refused(const std::function<void()>& f) {
  bool caught=false;try{f();}catch(const Refused&){caught=true;}
  require(caught,"unsafe attribution accepted");
}
Json evidence(const char* outcome="known",const char* id="person-a") {
  auto out=object();put(out,"outcome",string(outcome));put(out,"speaker_id",string(id));
  put(out,"label",string("Fixture speaker"));put(out,"reason",string("fixture_match"));return out;
}
CleanEvidence clean(const FinalKey& k) {
  return {k,k.start,k.end,"1",std::string(64,'a'),std::string(64,'b')};
}
void transitions() {
  for(const char* result:{"known","unknown","unavailable"}) {
    Attributions a;a.begin("session-a");FinalKey k{"session-a","track-a",101,16000,48000};
    auto initial=a.add(3,k,10,true);
    require(str(field(initial.get(),"decision"))=="pending" &&
            integer(field(initial.get(),"revision"))==0 && a.pending()==1,
            "final did not carry explicit pending state");
    auto c=clean(k);auto out=a.resolve(3,k,evidence(result),std::string(result)=="unavailable"?nullptr:&c);
    require(str(field(out.get(),"decision"))==(std::string(result)=="unavailable"?"uncertain":result),
            "resolution changed decision");
    require(integer(field(out.get(),"refers_to"))==101 &&
            integer(field(out.get(),"start_sample"))==16000 &&
            integer(field(out.get(),"end_sample"))==48000 &&
            str(field(out.get(),"track_id"))=="track-a" &&
            integer(field(out.get(),"revision"))==1 && a.pending()==0 && a.size()==1,
            "resolution lost exact original final binding or duplicated final");
    auto duplicate=a.resolve(3,k,evidence(result),std::string(result)=="unavailable"?nullptr:&c);
    require(cJSON_IsNull(duplicate.get()),"identical amendment delivered twice");
    if(std::string(result)!="unavailable")refused([&]{a.resolve(3,k,evidence("known","person-b"),&c);});
  }
}
void hostile_bindings() {
  Attributions a;a.begin("session-a");FinalKey k{"session-a","track-a",101,0,32000};
  a.add(7,k,10,true);auto c=clean(k);
  for(unsigned which=0;which<5;++which) {
    auto wrong=k;
    if(which==0)wrong.session="session-b";
    if(which==1)wrong.sequence++;
    if(which==2)wrong.track="track-b";
    if(which==3)wrong.start++;
    if(which==4)wrong.end++;
    refused([&]{a.resolve(7,wrong,evidence(),&c);});
    auto forged=clean(wrong);
    refused([&]{a.resolve(7,wrong,evidence(),&forged);});
    auto bad=c;bad.final=wrong;refused([&]{a.resolve(7,k,evidence(),&bad);});
    require(a.pending()==1,"rejected amendment mutated pending final");
  }
  for(unsigned which=0;which<4;++which) {
    auto bad=c;if(which==0)bad.end++;
    if(which==1)bad.start=bad.end;
    if(which==2)bad.policy_sha256="";
    if(which==3)bad.enrollment_revision="";
    refused([&]{a.resolve(7,k,evidence(),&bad);});
  }
  refused([&]{a.begin("session-b");});
  a.end("session_aborted");a.begin("session-b");
  auto next=k;next.session="session-b";a.add(7,next,20,true);
  refused([&]{a.resolve(7,k,evidence(),&c);});
  require(a.pending()==1,"stale session mutated successor");
}
void mixture_and_overlap() {
  Attributions a;a.begin("session-a");
  FinalKey capture{"session-a","",1,0,32000};a.add(1,capture,1,true);
  auto mixed=a.resolve(1,capture,evidence());
  require(str(field(mixed.get(),"decision"))=="uncertain" &&
          str(field(mixed.get(),"reason"))=="speaker_track_unverified" &&
          encode(mixed).find("person-a")==std::string::npos &&
          encode(mixed).find("Fixture speaker")==std::string::npos,
          "whole-mixture identity escaped containment");
  FinalKey first{"session-a","track-a",2,40000,80000};auto c=clean(first);
  FinalKey second{"session-a","track-b",3,40000,80000};
  a.add(2,first,2,true);a.add(3,second,2,true);
  a.resolve(2,first,evidence(),&c);
  refused([&]{a.resolve(3,second,evidence(),&c);});
  auto unknown=a.resolve(3,second,evidence("unavailable"));
  require(str(field(unknown.get(),"decision"))=="uncertain" &&
          encode(unknown).find("person-a")==std::string::npos,
          "concurrent unknown track borrowed known track's identity");
}
void bounded_retirement() {
  Attributions a;a.begin("session-a");FinalKey k{"session-a","track-a",1,0,32000};
  a.add(1,k,100,true);require(a.expire(15099).empty(),"early attribution timeout");
  auto retired=a.expire(15100);require(retired.size()==1 && a.pending()==0,"timeout did not settle once");
  require(a.expire(15101).empty(),"timeout emitted twice");
  // Hundreds of later resolved finals cannot evict a timed-out model job's
  // reference while it can still return. Retained state stays bounded.
  for(uint64_t i=2;i<500;++i) {
    FinalKey later{"session-a","",i,i*32000,(i+1)*32000};a.add(i,later,15101,false);
  }
  require(a.size()==Attributions::capacity,"history not bounded");
  auto c=clean(k);require(cJSON_IsNull(a.resolve(1,k,evidence(),&c).get()),"late timeout verdict revived identity");
  a.begin("session-b");
  for(uint64_t i=1;i<=Attributions::pending_capacity;++i)
    a.add(i,FinalKey{"session-b","track-a",i,i*32000,(i+1)*32000},1,true);
  const auto overflow=Attributions::pending_capacity+1;
  refused([&]{a.add(overflow,FinalKey{"session-b","track-a",overflow,0,32000},1,true);});
  auto ended=a.end("session_failed");require(ended.size()==Attributions::pending_capacity && a.pending()==0,"failure lost pending finals");
  require(a.end("session_failed").empty(),"failure duplicated amendments");
}
void anonymous_projection() {
  Attributions a;a.begin("session");FinalKey key{"session","track-a",7,0,64000};a.add(1,key,0,true);
  auto detail=evidence("unavailable");put(detail,"speaker_uuid",string("12345678-1234-4234-8234-123456789abc"));
  put(detail,"registry_revision",string("3"));put(detail,"continuity",string("matched"));put(detail,"display_label",string("Chosen label"));
  put(detail,"private_vector",string("must-not-escape"));
  auto result=a.resolve(1,key,clone(detail.get()));
  require(str(field(result.get(),"speaker_uuid"))=="12345678-1234-4234-8234-123456789abc" &&
    str(field(result.get(),"decision"))=="uncertain" && !flag(field(result.get(),"used_for_permissions")),"UUID is not a person/authority assertion");
  auto snapshot=a.snapshot();auto* first=cJSON_GetArrayItem(snapshot.get(),0);
  require(str(field(first,"display_label"))=="Chosen label"&&str(field(first,"registry_revision"))=="3","UUID lost at status reconciliation");
  require(encode(result).find("must-not-escape")==std::string::npos,"private evidence leaked");
  require(cJSON_IsNull(a.resolve(1,key,std::move(detail)).get()),"duplicate anonymous attribution emitted twice");
}
void queued_matcher_backpressure() {
  Attributions a;a.begin("busy");
  // One matcher is running and its eight waiting slots are full. Every later
  // final still needs a pending row until its immediate unavailable arrives.
  for(uint64_t i=1;i<=speaker_queue_capacity+1;++i)
    a.add(i,FinalKey{"busy","track",i,0,32000},1,true);
  for(uint64_t i=speaker_queue_capacity+2;i<500;++i) {
    const FinalKey key{"busy","track",i,0,32000};
    a.add(i,key,1,true);
    a.resolve(i,key,evidence("unavailable"));
    require(a.pending()==speaker_queue_capacity+1,"backpressure lost an outstanding job");
  }
  require(a.end("session_aborted").size()==speaker_queue_capacity+1,"abort missed queued matches");
}
void vectors(const char* path) {
  std::ifstream input(path);require(bool(input),"attribution vectors missing");
  std::ostringstream bytes;bytes<<input.rdbuf();auto doc=parse(bytes.str());
  const auto* cases=field(doc.get(),"cases");require(cJSON_IsArray(cases) && cJSON_GetArraySize(cases)==5,"attribution vector census");
  for(const auto* row=cases->child;row;row=row->next) {
    Attributions a;const auto sid=str(field(row,"session_id"));a.begin(sid);
    const auto* track=field(row,"track_id");require(cJSON_IsString(track),"vector track string required");
    FinalKey k{sid,track->valuestring,integer(field(row,"final_sequence")),
               integer(field(row,"start_sample")),integer(field(row,"end_sample"))};
    const auto core=integer(field(row,"core_sequence"));auto initial=a.add(core,k,10,true);
    require(cJSON_Compare(initial.get(),field(row,"expected_initial"),true),"initial vector differs");
    auto c=clean(k);const auto outcome=str(field(row,"outcome"));
    auto result=a.resolve(core,k,evidence(outcome.c_str()),flag(field(row,"clean_evidence"))?&c:nullptr);
    require(cJSON_Compare(result.get(),field(row,"expected_amendment"),true),"amendment vector differs");
  }
}
int main(int argc,char** argv) {try {
  transitions();hostile_bindings();mixture_and_overlap();bounded_retirement();anonymous_projection();queued_matcher_backpressure();
  require(argc==2,"shared attribution vectors required");vectors(argv[1]);
  std::cout<<"pending transitions, exact segment binding, mixture containment, duplicate/stale fences and bounded retirement PASS\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
