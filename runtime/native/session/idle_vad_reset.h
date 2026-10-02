#pragma once
#include <cstddef>
#include <cstdint>
#include <stdexcept>
namespace aii::voice {
// Candidate control-owner policy. It never changes the capture/source clock,
// and cannot count zero padding on a partial final block as received silence.
class IdleVadReset {
 public:
  bool observe(bool speech,size_t valid_samples) {
    if(!valid_samples || valid_samples>512)throw std::invalid_argument("VAD idle counter needs a valid input block");
    if(speech) { quiet_=0;return false; }
    quiet_+=valid_samples;
    if(quiet_<80000)return false;
    quiet_=0;return true;
  }
 private:
  uint32_t quiet_=0;
};
}
