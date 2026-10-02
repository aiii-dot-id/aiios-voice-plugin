// end_process ends the worker without library teardown (worker_io.h): a child
// that has loaded a library whose process-detach work takes 30 s, then ends by
// end_process(23), must be gone with exit code 23 well within the carrier's
// five-second retirement bound. The same child ending by std::_Exit, exit()
// or a return from main would wait for that teardown.
#include "worker_io.h"
#include <chrono>
#include <cstdio>
#include <string>
#define NOMINMAX
#include <windows.h>

extern "C" __declspec(dllimport) void aii_worker_exit_test_arm_teardown(unsigned milliseconds);

int main(int argc, char **argv) {
  if (argc == 2 && std::string(argv[1]) == "child") {
    aii_worker_exit_test_arm_teardown(30000);
    aii::voice::wire::end_process(23);
  }
  wchar_t self[MAX_PATH];
  if (!GetModuleFileNameW(nullptr, self, MAX_PATH))
    return 2;
  std::wstring command = L"\"" + std::wstring(self) + L"\" child";
  STARTUPINFOW startup{};
  startup.cb = sizeof startup;
  PROCESS_INFORMATION child{};
  const auto started = std::chrono::steady_clock::now();
  if (!CreateProcessW(nullptr, command.data(), nullptr, nullptr, FALSE, 0, nullptr, nullptr, &startup, &child))
    return 3;
  const DWORD waited = WaitForSingleObject(child.hProcess, 10000);
  const double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
  DWORD code = 0;
  GetExitCodeProcess(child.hProcess, &code);
  if (waited != WAIT_OBJECT_0)
    TerminateProcess(child.hProcess, 1);
  CloseHandle(child.hThread);
  CloseHandle(child.hProcess);
  if (waited != WAIT_OBJECT_0) {
    std::printf("FAIL: the child was still running after %.3f s: its end waited for library teardown\n", seconds);
    return 1;
  }
  std::printf("child ended in %.3f s with exit code %lu\n", seconds, static_cast<unsigned long>(code));
  if (code != 23 || seconds >= 3) {
    std::printf("FAIL: expected exit code 23 within 3 s\n");
    return 1;
  }
  return 0;
}
