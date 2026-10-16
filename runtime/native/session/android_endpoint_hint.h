#pragma once
#if !defined(__ANDROID__)
#error "Android endpoint hints require Android"
#endif
#include "endpoint_hint.h"
#include <android/performance_hint.h>
#include <dlfcn.h>
#include <unistd.h>
#include <type_traits>

namespace aii::voice {
// Public API-33 functions are resolved at runtime; the rest of the engine can
// retain its earlier Android API floor. An explicitly selected candidate fails
// visibly if hints are unavailable, rather than silently claiming their use.
class AndroidEndpointHint {
  using Manager=APerformanceHintManager*(*)();
  using Create=APerformanceHintSession*(*)(APerformanceHintManager*,const int32_t*,size_t,int64_t);
  using Report=int(*)(APerformanceHintSession*,int64_t);
  using Close=void(*)(APerformanceHintSession*);
  using Rate=int64_t(*)(APerformanceHintManager*);
#if __ANDROID_API__ >= 33
  static_assert(std::is_same_v<Manager,decltype(&APerformanceHint_getManager)>);
  static_assert(std::is_same_v<Create,decltype(&APerformanceHint_createSession)>);
  static_assert(std::is_same_v<Report,decltype(&APerformanceHint_reportActualWorkDuration)>);
  static_assert(std::is_same_v<Close,decltype(&APerformanceHint_closeSession)>);
  static_assert(std::is_same_v<Rate,decltype(&APerformanceHint_getPreferredUpdateRateNanos)>);
#endif
 public:
  AndroidEndpointHint() {
    library_=dlopen("libandroid.so",RTLD_NOW|RTLD_LOCAL);
    if(!library_) throw std::runtime_error("Android performance hint library unavailable");
    manager_=symbol<Manager>("APerformanceHint_getManager");
    create_=symbol<Create>("APerformanceHint_createSession");
    report_=symbol<Report>("APerformanceHint_reportActualWorkDuration");
    close_=symbol<Close>("APerformanceHint_closeSession");
    rate_=symbol<Rate>("APerformanceHint_getPreferredUpdateRateNanos");
    if(!manager_ || !create_ || !report_ || !close_ || !rate_) {
      dlclose(library_);library_=nullptr;
      throw std::runtime_error("Android performance hint API unavailable");
    }
  }
  ~AndroidEndpointHint(){close();if(library_)dlclose(library_);}
  AndroidEndpointHint(const AndroidEndpointHint&)=delete;
  AndroidEndpointHint& operator=(const AndroidEndpointHint&)=delete;
  bool open(int32_t tid,int64_t target_ns) {
    if(session_) throw std::logic_error("endpoint hint already open");
    auto* manager=manager_();if(!manager)return false;
    preferred_rate_ns=rate_(manager);
    session_=create_(manager,&tid,1,target_ns);
    return session_!=nullptr;
  }
  int report(int64_t actual_ns){return report_(session_,actual_ns);}
  void close()noexcept{if(session_){close_(session_);session_=nullptr;}}
  int64_t preferred_rate_ns=0;
 private:
  template<class T>T symbol(const char* name){return reinterpret_cast<T>(dlsym(library_,name));}
  void* library_=nullptr;
  APerformanceHintSession* session_=nullptr;
  Manager manager_=nullptr;
  Create create_=nullptr;
  Report report_=nullptr;
  Close close_=nullptr;
  Rate rate_=nullptr;
};
}
