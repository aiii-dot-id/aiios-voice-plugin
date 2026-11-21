#include "worker_dumps.h"
#ifdef _WIN32
#include <windows.h>
#include <werapi.h>
#else
#include <sys/resource.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif
#endif
#include <cstdio>
#ifndef _WIN32
#include <unistd.h>
#endif
int main() {
  int failed = 0;
#ifndef _WIN32
  aii::voice::disable_crash_dumps();
  rlimit now{};
  if (getrlimit(RLIMIT_CORE, &now) != 0 || now.rlim_cur != 0 || now.rlim_max != 0) { std::fputs("FAILED: a core can still be written\n", stderr); ++failed; }
  const rlimit raise{1 << 20, 1 << 20};
  if (geteuid() != 0 && setrlimit(RLIMIT_CORE, &raise) == 0) { std::fputs("FAILED: the core limit could be raised again\n", stderr); ++failed; }
  if (geteuid() == 0) std::puts("worker dumps: privileged, so the raise is not checked here");
#ifdef __linux__
  if (prctl(PR_GET_DUMPABLE, 0, 0, 0, 0) != 0) { std::fputs("FAILED: the process is still dumpable\n", stderr); ++failed; }
#endif
#endif
#ifdef _WIN32
  aii::voice::disable_crash_dumps();
  if (!(GetErrorMode() & SEM_NOGPFAULTERRORBOX)) { std::fputs("FAILED: a fault would show a dialog\n", stderr); ++failed; }
  DWORD flags = 0;
  if (FAILED(WerGetFlags(GetCurrentProcess(), &flags)) || !(flags & WER_FAULT_REPORTING_FLAG_NOHEAP)) {
    std::fputs("FAILED: a fault report would carry the heap\n", stderr); ++failed;
  }
  const auto ours = SetUnhandledExceptionFilter(nullptr);
  if (reinterpret_cast<const void*>(ours) != aii::voice::unreported_exception_filter()) { std::fputs("FAILED: an unhandled exception would be reported\n", stderr); ++failed; }
  SetUnhandledExceptionFilter(ours);
  if (!failed) std::puts("worker dumps: no fault dialog, no heap in a report, an unhandled exception ends the process unreported");
#else
  if (!failed) std::puts(geteuid() == 0 ? "worker dumps: no core can be written" : "worker dumps: no core can be written, nor the limit raised again");
#endif
  return failed;
}
