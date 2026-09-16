cmake_minimum_required(VERSION 3.22)
project(aii_pocket_native LANGUAGES C CXX)
foreach(required ENGINE_SOURCE RESIDENT_SOURCE)
  if(NOT IS_DIRECTORY "${${required}}")
    message(FATAL_ERROR "Existing, explicitly bound ${required} required")
  endif()
endforeach()
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
add_compile_definitions(GGML_GELU_FORCE_F32)
# Upstream builds model objects in separate targets. Apply the bound helper
# include path before creating them, ahead of POSIX's unrelated <paths.h>.
include_directories(BEFORE "${RESIDENT_SOURCE}")
add_subdirectory("${ENGINE_SOURCE}" engine)
target_include_directories(engine_runtime PRIVATE "${RESIDENT_SOURCE}")
target_sources(engine_runtime PRIVATE "${RESIDENT_SOURCE}/profiling.cpp")
add_library(native_pocket_resident SHARED
  "${RESIDENT_SOURCE}/resident.cpp" "${RESIDENT_SOURCE}/assets_bound.cpp")
target_compile_features(native_pocket_resident PRIVATE cxx_std_17)
target_include_directories(native_pocket_resident PRIVATE
  "${ENGINE_SOURCE}/include" "${ENGINE_SOURCE}/external/ggml/include"
  "${ENGINE_SOURCE}/src/models/pocket_tts")
target_link_libraries(native_pocket_resident PRIVATE engine_runtime ggml)
if(ANDROID)
  find_library(ANDROID_LOG_LIBRARY log REQUIRED)
  target_link_libraries(native_pocket_resident PRIVATE ${ANDROID_LOG_LIBRARY})
endif()
