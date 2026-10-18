#include <windows.h>
namespace {
DWORD teardown_milliseconds = 0;
struct SlowTeardown {
  ~SlowTeardown() {
    if (teardown_milliseconds)
      Sleep(teardown_milliseconds);
  }
} slow_teardown;
}
extern "C" __declspec(dllexport) void aii_worker_exit_test_arm_teardown(unsigned milliseconds) {
  teardown_milliseconds = milliseconds;
}
