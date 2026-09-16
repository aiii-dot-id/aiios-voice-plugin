#include "pause_gate.h"
#include <array>
#include <iostream>
#include <string>
using Gate=aii::endpoint::PauseGate;
using Decision=Gate::Decision;
void need(bool value,const char* text){if(!value)throw std::runtime_error(text);}
struct Rig {
  std::vector<std::shared_ptr<std::promise<double>>> jobs;
  std::vector<Gate::Event> events;
  Gate gate{[&](uint64_t,std::vector<float> p){
    need(!p.empty()&&p.size()<=128000,"recheck context bound");
    auto job=std::make_shared<std::promise<double>>();jobs.push_back(job);return job->get_future().share();
  },[&](const Gate::Event& e){events.push_back(e);}};
  uint64_t position=0,silence=0;
  Decision step(bool speech=false){
    std::array<float,512> p;p.fill(float(position));gate.append(p.data(),p.size());
    position+=512;silence=speech?0:silence+512;return gate.poll(speech,silence,position);
  }
  void to(uint64_t target){while(silence<target)need(step()==Decision::None,"unexpected early turn");}
};
int main(){try{
  need(Gate::hold_threshold==.5,"wrong candidate confidence");
  {
    Rig r;r.step(true);r.to(10240);need(r.jobs.size()==1,"initial query missing");
    r.jobs[0]->set_value(.5);r.to(12288);need(!r.gate.pending(),"equality did not hold");
    r.to(13824);need(r.jobs.size()==1,"recheck lacked fresh audio");
    r.to(14336);need(r.jobs.size()==2,"fresh-context recheck absent");
    r.jobs[1]->set_value(.8);r.to(15872);
    need(r.step()==Decision::SemanticNoHold,"recheck did not commit at its own horizon");
    need(r.events.back().resolution_position-r.events.back().query_position==2048,"recheck lost provisional interval");r.gate.close();
  }
  {
    Rig r;r.step(true);size_t answered=0;
    while(r.silence<30208){
      need(r.step()==Decision::None,"hold ended early");
      if(r.jobs.size()>answered){r.jobs.back()->set_value(0.);answered=r.jobs.size();}
    }
    need(r.step()==Decision::BoundedSilence,"bounded silence lost");
    need(r.jobs.size()==5,"more than five queries in one pause");r.gate.close();
  }
  {
    Rig r;r.step(true);r.to(10240);r.jobs[0]->set_value(0.);r.to(14336);r.jobs[1]->set_value(.9);
    r.to(15872);need(r.step(true)==Decision::None,"speech at recheck horizon lost");
    need(r.events.back().stale,"resumed speech accepted stale score");r.to(10240);
    need(r.jobs.size()==3,"resumption did not reset query budget");r.jobs[2]->set_value(.9);r.gate.close();
  }
  {
    Rig r;r.step(true);r.to(10240);r.jobs[0]->set_value(0.);r.to(14336);r.to(15872);
    bool refused=false;try{r.step();}catch(const std::runtime_error& e){refused=std::string(e.what()).find("250ms")!=std::string::npos;}
    need(refused && r.gate.outstanding()==1,"recheck timeout guessed a turn or lost ownership");
    r.jobs[1]->set_value(.9);r.gate.close();
  }
  {
    Rig r;r.step(true);r.to(10240);r.jobs[0]->set_value(0.);r.to(14336);r.step(true);
    r.jobs[1]->set_exception(std::make_exception_ptr(std::runtime_error("recheck model fault")));
    bool refused=false;try{r.gate.close();}catch(const std::runtime_error& e){refused=std::string(e.what())=="recheck model fault";}
    need(refused && r.gate.outstanding()==1,"stale recheck failure was erased");
  }
  std::cout<<"bounded rechecks: fresh audio, exact horizons, inclusive speech, query cap, deadline and stale-fault custody PASS\n";
  return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
