#pragma once
#include "../../native_echo/echo.h"
#include <array>
#include <stdexcept>
#include <vector>

namespace aii::voice {
// Serial worker-owned adapter. Retains at most one 10 ms input fragment and
// the DSP's one output frame. The caller retains prepared output on AGAIN;
// it must not submit the same acoustic samples to the adapter twice.
class EchoInput {
  aii_echo* echo_ = nullptr;
  uint64_t generation_, received_ = 0, submitted_ = 0, emitted_ = 0;
  size_t held_ = 0;
  bool finished_ = false;
  std::array<float,160> mic_{}, ref_{}, out_{};
  static void check(int rc) { if(rc!=AII_ECHO_OK) throw std::runtime_error("native echo processing failed"); }
  void process(std::vector<float>& output) {
    size_t n=0;
    check(aii_echo_process(echo_,generation_,submitted_,mic_.data(),ref_.data(),held_,AII_ECHO_REFERENCE_VALID,out_.data(),&n));
    submitted_+=held_;held_=0;emitted_+=n;
    output.insert(output.end(),out_.begin(),out_.begin()+n);
  }
public:
  explicit EchoInput(uint64_t generation):generation_(generation) { check(aii_echo_create(generation,&echo_)); }
  ~EchoInput(){aii_echo_destroy(echo_);}
  EchoInput(const EchoInput&)=delete;
  EchoInput& operator=(const EchoInput&)=delete;
  uint64_t emitted()const{return emitted_;}
  bool finished()const{return finished_;}
  std::vector<float> feed(uint64_t start,const float* pairs,size_t samples) {
    if(finished_||start!=received_||(!pairs&&samples))throw std::runtime_error("echo input clock differs");
    std::vector<float> output;output.reserve(samples+160);
    for(size_t i=0;i<samples;i++) {
      mic_[held_]=pairs[i*2];ref_[held_]=pairs[i*2+1];++held_;
      if(held_==160)process(output);
    }
    received_+=samples;return output;
  }
  std::vector<float> finish() {
    std::vector<float> output;
    if(finished_)return output;
    if(held_)process(output);
    size_t n=0;check(aii_echo_finish(echo_,generation_,out_.data(),&n));
    output.insert(output.end(),out_.begin(),out_.begin()+n);emitted_+=n;finished_=true;
    if(emitted_!=received_)throw std::runtime_error("echo tail count differs");
    return output;
  }
  aii_echo_status status()const{aii_echo_status s{};check(aii_echo_get_status(echo_,&s));return s;}
};
}
