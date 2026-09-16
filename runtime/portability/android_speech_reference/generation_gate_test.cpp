#include "native_generation_gate.h"
#include <chrono>
#include <future>
#include <iostream>
#include <stdexcept>
#include <thread>
using aii::voice::pixel::GenerationGate;
void demand(bool value,const char* why){if(!value)throw std::runtime_error(why);}
template<class F> bool refused(F f){try{f();return false;}catch(const std::runtime_error&){return true;}}
int main(){try{
  for(int repeat=0;repeat<200;++repeat){
    GenerationGate gate;gate.cancel(1);
    demand(refused([&]{gate.admit(1);}),"pre-cancelled generation admitted");
    gate.admit(2);
    std::promise<void> active,release;
    auto resume=release.get_future();
    auto recognition=std::async(std::launch::async,[&]{
      active.set_value();resume.wait();return refused([&]{gate.check(2);});
    });
    active.get_future().wait();
    auto control=std::async(std::launch::async,[&]{gate.cancel(2);});
    demand(control.wait_for(std::chrono::milliseconds(250))==std::future_status::ready,"cancel waited behind inference");
    demand(recognition.wait_for(std::chrono::milliseconds(0))!=std::future_status::ready,"held owner retired early");
    release.set_value();control.get();
    demand(recognition.get(),"cancelled generation published");
    gate.cancel(2);gate.admit(3);gate.check(3);
    demand(refused([&]{gate.admit(3);}),"generation reuse admitted");
    demand(refused([&]{gate.check(2);}),"old generation revived");
    gate.close();gate.cancel(5);
    demand(refused([&]{gate.admit(6);}),"closed owner admitted work");
    demand(refused([&]{gate.check(3);}),"closed owner published");
  }
  std::cout<<"200 generation fence lifecycles passed\n";return 0;
}catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}}
