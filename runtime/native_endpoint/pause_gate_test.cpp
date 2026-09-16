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
      const bool held=!finished.load();
      // An independent capture/control lane can act while this consumer waits.
      std::atomic<bool> interruption{false}; interruption=true;
      r.jobs[0]->set_value(.9); consumer.join();
      if(error) std::rethrow_exception(error);
      require(held && interruption && decision==Decision::SemanticNoHold && r.events.back().resolution_position==12800,"late inference moved the audio boundary");
      r.gate.close();
    }
    {
      Rig r; r.gate.poll(false,10240,10752); bool timeout=false;
      try { r.gate.poll(false,12288,12800); } catch(const std::runtime_error& e) {
        timeout=std::string(e.what()).find("no turn committed")!=std::string::npos;
      }
      require(timeout && r.gate.outstanding()==1,"timeout guessed completion or lost query");
      r.jobs[0]->set_value(.9); r.gate.close();
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
    std::cout<<"pause clock: horizon, inclusive speech, hold, bounded silence, retained context, ownership, timeout and faults passed\n";
    return 0;
  } catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
