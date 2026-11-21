#include "c_api.h"
#include <cstdio>
#include <cstring>

int main() {
  std::fputs("probe: main\n", stderr);
  std::fflush(stderr);
  const aii_voice_paths paths{};
  aii_voice_error error{};
  const aii_voice_result result = aii_voice_model_server_serve(&paths, "cpu", nullptr, nullptr, 0, nullptr, nullptr, 0,
                                                               1, 1, 64, 0, 1, nullptr, nullptr, 0, 0, &error);
  std::printf("probe: the library answered %d: %s\n", int(result), error.message);
  std::fflush(stdout);
  return result == AII_VOICE_INVALID && std::strstr(error.message, "readiness callback") ? 0 : 1;
}
