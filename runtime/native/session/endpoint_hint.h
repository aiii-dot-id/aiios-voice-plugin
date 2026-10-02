#pragma once
#include <cstdint>
#include <stdexcept>

namespace aii::voice {
// One owner, on the same long-lived inference thread. No reports for idle time,
// artificial work, or a requested target masquerading as an observation.
template<class Backend> class EndpointWorkHint {
 public:
  EndpointWorkHint(Backend& backend, int32_t tid, int64_t target_ns):backend_(backend) {
    if(tid<=0 || target_ns<=0) throw std::invalid_argument("invalid endpoint hint target/thread");
    if(!backend_.open(tid,target_ns)) throw std::runtime_error("endpoint performance hint unavailable");
  }
  ~EndpointWorkHint(){backend_.close();}
  EndpointWorkHint(const EndpointWorkHint&)=delete;
  EndpointWorkHint& operator=(const EndpointWorkHint&)=delete;
  void report(int64_t actual_ns) {
    if(actual_ns<=0) throw std::invalid_argument("positive measured endpoint work required");
    if(backend_.report(actual_ns)!=0) throw std::runtime_error("endpoint performance hint report failed");
  }
 private:
  Backend& backend_;
};
}
