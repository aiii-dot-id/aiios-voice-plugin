#include "cancel_result.h"
#include <iostream>

int main() {
  const char* terminated = "Exiting due to terminate flag being set to true.";
  if (!uid_detail::run_was_cancelled(true, ORT_FAIL, terminated)) {
    std::cerr << "the pinned termination status was not recognized\n"; return 1;
  }
  if (uid_detail::run_was_cancelled(false, ORT_FAIL, terminated) ||
      uid_detail::run_was_cancelled(true, ORT_RUNTIME_EXCEPTION, terminated) ||
      uid_detail::run_was_cancelled(true, ORT_FAIL, "unrelated model failure") ||
      uid_detail::run_was_cancelled(true, ORT_FAIL, nullptr) ||
      uid_detail::run_was_cancelled(true, ORT_FAIL, "Exiting due to terminate flag being set to true. plus another error")) {
    std::cerr << "cancellation concealed a model failure\n"; return 1;
  }
  std::cout << "cancellation/fault classification passed\n";
  return 0;
}
