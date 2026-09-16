# The common session must never silently resolve an older component checkout.
if(NOT DEFINED AII_ASR_LIBRARY OR NOT EXISTS "${AII_ASR_LIBRARY}")
  message(FATAL_ERROR "Explicit tested AII_ASR_LIBRARY required; no retained-component fallback")
endif()
include(CheckCXXSourceCompiles)
include(CMakePushCheckState)
cmake_push_check_state(RESET)
# CheckCXXSourceCompiles writes this value into a generated CMakeLists.txt.
# A native Windows backslash path would be reparsed as escapes (e.g. \w).
# Normalize only its spelling; keep checking the exact explicitly selected file.
file(TO_CMAKE_PATH "${AII_ASR_LIBRARY}" _aii_asr_check_library)
set(CMAKE_REQUIRED_LIBRARIES "${_aii_asr_check_library}")
set(CMAKE_REQUIRED_INCLUDES "${CMAKE_CURRENT_LIST_DIR}/../../native_asr")
# A changed library at the same path must be rechecked, not reuse cached success.
unset(AII_ASR_BUFFER_ABI_LINKS CACHE)
check_cxx_source_compiles("#include <asr.h>
int main() { AiiAsrBufferStats stats{}; size_t required=0;
  auto* model=aii_asr_create_configured(nullptr,nullptr,0,4,nullptr,nullptr,0);
  return aii_asr_buffer_stats(nullptr, &stats, nullptr, 0) +
    aii_asr_execution_info(model,nullptr,0,&required,nullptr,0); }
" AII_ASR_BUFFER_ABI_LINKS)
cmake_pop_check_state()
unset(_aii_asr_check_library)
if(NOT AII_ASR_BUFFER_ABI_LINKS)
  message(FATAL_ERROR "Selected ASR library lacks the required buffer-accounting/configuration/readback ABI")
endif()
