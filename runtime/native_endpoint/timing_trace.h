#pragma once
#include <cstdint>
#ifdef AII_ENDPOINT_TIMING_TRACE
#include <atomic>
#include <chrono>
#include <cstdio>
#if defined(__linux__)
#include <sched.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#endif
#endif

namespace aii::endpoint::timing {
// Diagnostic build only. No text, audio, vectors, addresses or identity data.
// Steady-clock timestamps share one process clock across queue/model/gate.
// This is observation, never an alternate executor or a deadline override.
inline uint64_t now() noexcept {
#ifdef AII_ENDPOINT_TIMING_TRACE
  return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count());
#else
  return 0;
#endif
}
inline uint64_t owner() noexcept {
#ifdef AII_ENDPOINT_TIMING_TRACE
  static std::atomic<uint64_t> next{0};return ++next;
#else
  return 0;
#endif
}
inline void record_named(const char* tag,uint64_t session,uint64_t query,const char* phase,
    uint64_t origin=0,uint64_t deadline=0,uint64_t samples=0) noexcept {
#ifdef AII_ENDPOINT_TIMING_TRACE
  const auto at=now();
  long tid=-1,cpu=-1,voluntary=-1,involuntary=-1;long long thread_cpu=-1;
#if defined(__linux__)
  tid=::syscall(SYS_gettid);cpu=::sched_getcpu();
  timespec elapsed{};
  if (::clock_gettime(CLOCK_THREAD_CPUTIME_ID,&elapsed)==0)
    thread_cpu=static_cast<long long>(elapsed.tv_sec)*1000000000LL+elapsed.tv_nsec;
  rusage usage{};
  if (::getrusage(RUSAGE_THREAD,&usage)==0){voluntary=usage.ru_nvcsw;involuntary=usage.ru_nivcsw;}
#endif
  std::fprintf(stderr,"%s {\"owner\":%llu,\"query\":%llu,\"phase\":\"%s\","
      "\"at_ns\":%llu,\"origin_ns\":%llu,\"deadline_ns\":%llu,\"samples\":%llu,"
      "\"tid\":%ld,\"cpu\":%ld,\"thread_cpu_ns\":%lld,\"voluntary\":%ld,\"involuntary\":%ld}\n",
      tag,(unsigned long long)session,(unsigned long long)query,phase,
      (unsigned long long)at,(unsigned long long)origin,(unsigned long long)deadline,(unsigned long long)samples,
      tid,cpu,thread_cpu,voluntary,involuntary);
#else
  (void)tag;(void)session;(void)query;(void)phase;(void)origin;(void)deadline;(void)samples;
#endif
}
inline void record(uint64_t session,uint64_t query,const char* phase,
    uint64_t origin=0,uint64_t deadline=0,uint64_t samples=0) noexcept {
  record_named("AII_ENDPOINT_GATE",session,query,phase,origin,deadline,samples);
}
}
