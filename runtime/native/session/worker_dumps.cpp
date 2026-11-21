#include "worker_dumps.h"
#include <stdexcept>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <werapi.h>
#include <csignal>
#include <cstdlib>
#else
#include <sys/resource.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif
#endif

namespace aii::voice {
#ifdef _WIN32
namespace {
LONG WINAPI end_unreported(EXCEPTION_POINTERS*) { TerminateProcess(GetCurrentProcess(), 3); return EXCEPTION_EXECUTE_HANDLER; }
void end_on_abort(int) { TerminateProcess(GetCurrentProcess(), 3); }
}
const void* unreported_exception_filter() { return reinterpret_cast<const void*>(&end_unreported); }
#endif
void disable_crash_dumps() {
#ifdef _WIN32
  _set_abort_behavior(0, _WRITE_ABORT_MSG | _CALL_REPORTFAULT);
  SetErrorMode(GetErrorMode() | SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
  SetUnhandledExceptionFilter(end_unreported);
  std::signal(SIGABRT, end_on_abort);
  if (FAILED(WerSetFlags(WER_FAULT_REPORTING_FLAG_NOHEAP))) throw std::runtime_error("crash reports cannot be limited");
#else
  const rlimit none{0, 0};
  if (setrlimit(RLIMIT_CORE, &none) != 0) throw std::runtime_error("core dumps cannot be turned off");
#ifdef __linux__
  if (prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0) throw std::runtime_error("the process cannot be made undumpable");
#endif
#endif
}
}
