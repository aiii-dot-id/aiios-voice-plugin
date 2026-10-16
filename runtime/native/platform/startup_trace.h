#pragma once
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>

namespace aii::platform {
// Opt-in private diagnostics. Labels are fixed source literals, never paths,
// speech, settings or credentials. The public SDK readiness contract is unchanged.
class StartupSpan {
  using Clock = std::chrono::steady_clock;
  const char* label_;
  const char* component_;
  bool enabled_;
  int exceptions_;
  Clock::time_point began_;
public:
  explicit StartupSpan(const char* label, const char* component="native-startup-profile") noexcept
      :label_(label),component_(component),enabled_(false),exceptions_(std::uncaught_exceptions()),began_(Clock::now()) {
    const char* value=std::getenv("AII_VOICE_STARTUP_TRACE");
    enabled_=value&&std::strcmp(value,"1")==0;
  }
  ~StartupSpan() noexcept {
    if(!enabled_)return;
    const auto us=std::chrono::duration_cast<std::chrono::microseconds>(Clock::now()-began_).count();
    std::fprintf(stderr,"{\"component\":\"%s\",\"phase\":\"%s\",\"microseconds\":%lld,\"completed\":%s}\n",
        component_,label_,static_cast<long long>(us),std::uncaught_exceptions()==exceptions_?"true":"false");
  }
};
}
