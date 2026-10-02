#include "worker_liveness.h"
#ifndef _WIN32
#include <sys/wait.h>
#include <unistd.h>
#include <cstdlib>
#include <string>

int main() {
  int ends[2];
  if (::pipe(ends) != 0) return 1;
  const pid_t child = ::fork();
  if (child < 0) return 2;
  if (child == 0) {
    ::close(ends[1]);
    const auto name = std::to_string(ends[0]);
    if (::setenv("AII_VOICE_CARRIER_LIVENESS_FD", name.c_str(), 1) != 0)
      std::_Exit(3);
    aii::voice::watch_carrier_liveness();
    ::sleep(2);
    std::_Exit(4); // Watcher failed to see carrier EOF.
  }
  ::close(ends[0]);
  ::close(ends[1]);
  int status = 0;
  if (::waitpid(child, &status, 0) != child) return 5;
  return WIFEXITED(status) && WEXITSTATUS(status) == 74 ? 0 : 6;
}
#else
int main() { return 0; }
#endif
