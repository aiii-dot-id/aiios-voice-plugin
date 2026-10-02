# A Linux runtime with placement readback cannot bind an older silent adapter.
include(CheckCXXSourceCompiles)
include(CMakePushCheckState)
cmake_push_check_state(RESET)
set(CMAKE_REQUIRED_LIBRARIES "${AII_POCKET_LIBRARY}")
unset(AII_POCKET_PLACEMENT_ABI_LINKS CACHE)
check_cxx_source_compiles("#include <cstddef>
extern \"C\" int nv_execution_info(void*,char*,std::size_t) noexcept;
int main() { char value[4096]{}; return nv_execution_info(nullptr,value,sizeof value); }
" AII_POCKET_PLACEMENT_ABI_LINKS)
cmake_pop_check_state()
if(NOT AII_POCKET_PLACEMENT_ABI_LINKS)
  message(FATAL_ERROR "Selected Pocket library lacks the required Linux device-placement readback ABI")
endif()

cmake_push_check_state(RESET)
set(CMAKE_REQUIRED_LIBRARIES "${AII_POCKET_LIBRARY}")
unset(AII_POCKET_VOICE_ABI_LINKS CACHE)
check_cxx_source_compiles("#include <cstddef>
extern \"C\" int nv_configure_voice(void*,const char*,float,char*,std::size_t) noexcept;
int main() { char value[4096]{}; return nv_configure_voice(nullptr,\"alba\",0.3f,value,sizeof value); }
" AII_POCKET_VOICE_ABI_LINKS)
cmake_pop_check_state()
if(NOT AII_POCKET_VOICE_ABI_LINKS)
  message(FATAL_ERROR "Selected Pocket library lacks the required voice-configuration ABI")
endif()
