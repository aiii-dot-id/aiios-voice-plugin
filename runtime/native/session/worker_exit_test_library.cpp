// Test library for aii_worker_exit_test: process-detach work that takes
// seconds once armed, as torch_cpu's operator deregistration does when the
// worker's process exits through ExitProcess.
#include <windows.h>
namespace {
DWORD teardown_milliseconds = 0;
struct SlowTeardown {
  ~SlowTeardown() {
    if (teardown_milliseconds)
      Sleep(teardown_milliseconds);
  }
} slow_teardown;
} // namespace
extern "C" __declspec(dllexport) void aii_worker_exit_test_arm_teardown(unsigned milliseconds) {
  teardown_milliseconds = milliseconds;
}
