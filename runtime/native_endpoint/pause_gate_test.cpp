#include "pause_gate.h"
#include <atomic>
#include <iostream>
#include <string>
#include <thread>

using Gate=aii::endpoint::PauseGate;
using Decision=Gate::Decision;
static void require(bool ok,const char* why) { if(!ok) throw std::runtime_error(why); }
struct Rig {
  std::vector<std::shared_ptr<std::promise<double>>> jobs;
  std::vector<std::vector<float>> submitted;
  std::vector<Gate::Event> events;
  Gate gate{[&](uint64_t,std::vector<float> audio) {
    submitted.push_back(std::move(audio));
    auto job=std::make_shared<std::promise<double>>(); jobs.push_back(job);
    return job->get_future().share();
  },[&](const Gate::Event& event) { events.push_back(event); }};
  Rig(uint32_t pause=0) {
    if(pause) gate.configure_pause(pause);
    std::vector<float> block(512,1); gate.append(block.data(),block.size());
  }
};
int main() {
  try {
    for(uint32_t pause: {320u,1200u,2000u,5000u}) {
      const uint64_t horizon=((uint64_t(pause)*16+511)/512)*512;
      Rig r(pause);
      require(r.gate.commitment()==horizon,"requested pause rounded down or ignored");
      require(r.gate.poll(false,horizon-2560,horizon-2048)==Decision::None && r.jobs.empty(),"configured query early");
      r.gate.poll(false,horizon-2048,horizon-1536); r.jobs[0]->set_value(.9);
      require(r.gate.poll(false,horizon-512,horizon)==Decision::None,"configured pause ended early");
      require(r.gate.poll(false,horizon,horizon+512)==Decision::SemanticNoHold,"configured pause ignored");
      bool refused=false;
      try { r.gate.configure_pause(1200); } catch(const std::invalid_argument&) {refused=true;}
      require(refused,"active pause changed"); r.gate.close();
      Rig hold(pause); hold.gate.poll(false,horizon-2048,horizon-1536); hold.jobs[0]->set_value(.001);
      require(hold.gate.poll(false,horizon,horizon+512)==Decision::None,"semantic hold lost");
      require(hold.gate.poll(false,horizon+17920,horizon+18432)==Decision::None,"semantic hold short");
      require(hold.gate.poll(false,horizon+18432,horizon+18944)==Decision::BoundedSilence,"semantic bound lost"); hold.gate.close();
    }
    for(uint32_t pause:{319u,5001u,std::numeric_limits<uint32_t>::max()}) {
      bool refused=false;
      try { Rig r(pause); } catch(const std::invalid_argument&) {refused=true;}
      require(refused,"invalid pause accepted");
    }
    {
      Rig r;
      require(r.gate.poll(false,9728,10240)==Decision::None && r.jobs.empty(),"early query");
      require(r.gate.poll(false,10240,10752)==Decision::None && r.jobs.size()==1,"query boundary shifted");
      r.jobs[0]->set_value(.9);
      require(r.gate.poll(false,10240,10752)==Decision::None,"worker completion became audio clock");
      require(r.gate.poll(false,11776,12288)==Decision::None,"provisional window lost");
      require(r.gate.poll(false,12288,12800)==Decision::SemanticNoHold,"audio horizon did not commit");
      require(r.events.back().resolution_position==12800,"commit position moved");
      r.gate.close();
    }
    {
      Rig r; r.gate.poll(false,10240,10752); r.jobs[0]->set_value(.9);
      require(r.gate.poll(true,0,12800)==Decision::None,"inclusive horizon speech lost");
      require(r.events.back().stale && r.events.back().resolution_position==12800,"stale result became current");
      r.gate.close();
    }
    for (double probability: {0.0,.001,.01,.010001,1.0}) {
      Rig r; r.gate.poll(false,10240,10752); r.jobs[0]->set_value(probability);
      const auto expected=probability>.01?Decision::SemanticNoHold:Decision::None;
      require(r.gate.poll(false,12288,12800)==expected,"hold threshold changed");
      if (expected==Decision::None) {
        require(r.gate.poll(false,30208,30720)==Decision::None,"hold ended before silence bound");
        require(r.gate.poll(false,30720,31232)==Decision::BoundedSilence,"silence bound lost");
      }
      require(r.jobs.size()==1,"repeated prediction in same pause"); r.gate.close();
    }
    {
      Rig r; std::vector<float> audio(512*400);
      for(size_t i=0;i<audio.size();++i) audio[i]=static_cast<float>(i);
      r.gate.append(audio.data(),audio.size()); r.gate.poll(false,10240,10752);
      require(r.gate.samples()==128000 && r.submitted.back().front()==76800 && r.submitted.back().back()==204799,"eight-second context changed");
      r.gate.reset(); require(r.gate.outstanding()==1 && r.gate.samples()==0,"reset erased inference ownership");
      r.jobs[0]->set_value(.8); r.gate.close(); require(r.events.back().stale,"reset accepted old result");
    }
    {
      Rig r; r.gate.poll(false,10240,10752);
      std::atomic<bool> entered{false},finished{false};
      Decision decision=Decision::None; std::exception_ptr error;
      std::thread consumer([&] {
        entered=true;
        try { decision=r.gate.poll(false,12288,12800); } catch(...) { error=std::current_exception(); }
        finished=true;
      });
      while(!entered) std::this_thread::yield();
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
      // An independent capture/control lane can act while this consumer waits.
      std::atomic<bool> interruption{false}; interruption=true;
      r.jobs[0]->set_value(.9); consumer.join();
      if(error) std::rethrow_exception(error);
      require(finished && interruption && decision==Decision::SemanticNoHold &&
              r.events.back().resolution_position==12800,
              "late inference moved the audio boundary or blocked interruption");
      r.gate.close();
    }
    {
      Rig r; r.gate.poll(false,10240,10752);
      auto model=std::async(std::launch::async,[&]{
        std::this_thread::sleep_for(std::chrono::milliseconds(350));r.jobs[0]->set_value(.9);
      });
      const auto start=Gate::Clock::now();
      const auto decision=r.gate.poll(false,12288,12800);
      model.get();
      require(decision==Decision::SemanticNoHold && r.events.back().resolution_position==12800,
              "350ms endpoint moved the audio-clock commitment");
      require(Gate::Clock::now()-start<std::chrono::milliseconds(700),
              "endpoint waited beyond its supplied verdict");
      r.gate.close();
    }
    {
      Rig r; r.gate.poll(false,10240,10752);
      require(r.gate.poll(false,12288,12800)==Decision::None &&
              !r.gate.pending() && r.gate.outstanding()==1,
              "one-second endpoint overrun guessed a turn, faulted or lost ownership");
      require(r.gate.poll(false,30720,31232)==Decision::BoundedSilence,
              "late endpoint defeated the acoustic maximum");
      r.gate.reset();r.jobs[0]->set_value(.9);r.gate.close();
      require(r.events.back().stale,"late result revived a bounded-silence turn");
    }
    for(double probability:{-1.0,1.01,std::numeric_limits<double>::quiet_NaN()}) {
      Rig r; r.gate.poll(false,10240,10752); r.jobs[0]->set_value(probability); bool refused=false;
      try { r.gate.poll(false,12288,12800); } catch(const std::runtime_error&) { refused=true; }
      require(refused && r.gate.outstanding()==1,"invalid probability became a turn");
    }
    {
      Rig r; r.gate.poll(false,10240,10752); r.gate.reset();
      r.jobs[0]->set_exception(std::make_exception_ptr(std::runtime_error("model fault")));
      bool refused=false;
      try { r.gate.close(); } catch(const std::runtime_error& e) { refused=std::string(e.what())=="model fault"; }
      require(refused && r.gate.outstanding()==1,"stale model failure became absence");
    }
    {
      // THE GATE'S TWO WAITS ARE ITS CALLER'S TO STATE, before any input and
      // never as nothing.
      Rig r;
      for(const bool decision:{true,false}) {
        Gate fresh([](uint64_t,std::vector<float>){return std::shared_future<double>();},[](const Gate::Event&){});
        bool refused=false;
        try { fresh.configure_waits(std::chrono::milliseconds(decision?0:300),std::chrono::milliseconds(decision?300:0)); }
        catch(const std::invalid_argument&) {refused=true;}
        require(refused,"a wait stated as nothing was taken");
      }
      bool refused=false;
      try { r.gate.configure_waits(std::chrono::milliseconds(300),std::chrono::milliseconds(300)); } catch(const std::invalid_argument&) {refused=true;}
      require(refused,"the waits changed after input");
    }
    {
      // A verdict is waited for the stated time and no shorter: 1300 ms here,
      // past the second the header keeps for a caller that states none. The
      // turn is not guessed, the query stays owned, and the gate says once
      // that the verdict was late.
      std::vector<std::shared_ptr<std::promise<double>>> jobs; std::vector<Gate::Event> events;
      Gate gate([&](uint64_t,std::vector<float>) {
        auto job=std::make_shared<std::promise<double>>(); jobs.push_back(job); return job->get_future().share();
      },[&](const Gate::Event& event) { events.push_back(event); });
      gate.configure_waits(std::chrono::milliseconds(1300),std::chrono::milliseconds(300));
      std::vector<float> block(512,1); gate.append(block.data(),block.size());
      const auto asked=Gate::Clock::now();
      gate.poll(false,10240,10752);
      require(gate.poll(false,12288,12800)==Decision::None && !gate.pending() && gate.outstanding()==1,
              "a late verdict guessed a turn, faulted or lost ownership");
      require(Gate::Clock::now()-asked>=std::chrono::milliseconds(1300),"the verdict was not waited for the stated time");
      size_t late=0; for(const auto& event:events) late+=event.kind==Gate::Event::Kind::Late;
      require(late==1 && events.back().kind==Gate::Event::Kind::Late && events.back().stale &&
              events.back().resolution_position==12800,"a late verdict was not said, once, where it was late");
      // And a query still owned at the input's end is waited for the stated
      // time: the refusal carries it, 300 ms and not the header's fifteen seconds.
      bool unretired=false;
      const auto closing=Gate::Clock::now();
      try { gate.close(); } catch(const Gate::Unretired& e) {
        unretired=e.waited==std::chrono::milliseconds(300) && std::string(e.what())=="semantic endpoint did not retire in 300 ms";
      }
      require(unretired && gate.outstanding()==1,"a query that did not retire was not refused with the stated wait, or was dropped");
      require(Gate::Clock::now()-closing<std::chrono::seconds(10),"a query at the input's end was waited for the header's time, not the stated one");
      jobs[0]->set_value(.9); gate.close();
      require(events.back().kind==Gate::Event::Kind::Resolution && events.back().stale,"the late verdict was not kept as stale evidence");
    }
    std::cout<<"pause clock: horizon, inclusive speech, hold, bounded silence, retained context, ownership, stated waits, timeout and faults passed\n";
    return 0;
  } catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
