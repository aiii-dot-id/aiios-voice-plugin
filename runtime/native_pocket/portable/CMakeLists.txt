cmake_minimum_required(VERSION 3.22)
project(aii_pocket_native LANGUAGES C CXX)
include(../../cmake/SourcePrivacy.cmake)
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
if(APPLE)
  # The shipped Apple build: Pocket only, no OpenMP runtime, the Apple-silicon
  # baseline CPU (not the build host's), and precompiled Metal kernels. A
  # sandbox that denies writes cannot keep Metal's shader cache, so compiling
  # embedded source cost every contained start seconds (30-40 s seen in the field).
  set(AUDIOCPP_MODEL_SET custom CACHE STRING "" FORCE)
  set(AUDIOCPP_MODELS pocket_tts CACHE STRING "" FORCE)
  set(AUDIOCPP_BUILD_NATIVE_MODEL_MANAGER OFF CACHE BOOL "" FORCE)
  set(ENGINE_ENABLE_OPENMP OFF CACHE BOOL "" FORCE)
  set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
  set(ENGINE_ENABLE_NATIVE_CPU OFF CACHE BOOL "" FORCE)
  set(ENGINE_ENABLE_METAL ON CACHE BOOL "" FORCE)
  set(GGML_METAL_EMBED_LIBRARY OFF CACHE BOOL "" FORCE)
  # The worker also holds NeMo's ggml, whose kernels differ, so this library's
  # kernels are found beside it under its own name, never as default.metallib.
  set(metal_loader "${ENGINE_SOURCE}/external/ggml/src/ggml-metal/ggml-metal-device.m")
  file(READ "${metal_loader}" loader)
  if(NOT loader MATCHES "libX.dylib -> libX.metallib")
    message(FATAL_ERROR "metal-library-beside.patch is not applied to ${metal_loader}")
  endif()
endif()
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
if(APPLE)
  # The kernels every Apple-silicon GPU compiles at runtime: bf16 present and
  # the tensor API absent (the worker disables it; see worker_environment.h).
  # Compiled from the source directory so no build path enters the library.
  set(metal_src "${ENGINE_SOURCE}/external/ggml/src")
  # The loader derives the name from the image: libX.dylib -> libX.metallib.
  set(metallib "$<TARGET_FILE_DIR:native_pocket_resident>/$<TARGET_FILE_PREFIX:native_pocket_resident>$<TARGET_FILE_BASE_NAME:native_pocket_resident>.metallib")
  set(metal_min "")
  if(CMAKE_OSX_DEPLOYMENT_TARGET)
    set(metal_min "-mmacosx-version-min=${CMAKE_OSX_DEPLOYMENT_TARGET}")
  endif()
  add_custom_command(TARGET native_pocket_resident POST_BUILD
    COMMAND xcrun -sdk macosx metal -O3 ${metal_min} -DGGML_METAL_HAS_BF16=1 -I .. -c ggml-metal.metal
            -o "${CMAKE_CURRENT_BINARY_DIR}/native_pocket_resident.air"
    COMMAND xcrun -sdk macosx metallib "${CMAKE_CURRENT_BINARY_DIR}/native_pocket_resident.air" -o "${metallib}"
    WORKING_DIRECTORY "${metal_src}/ggml-metal"
    COMMENT "Compiling the Pocket Metal kernels"
    VERBATIM)
endif()
if(UNIX AND NOT APPLE)
  # A failed shader generator must fail this build, not the user's first load.
  target_link_options(native_pocket_resident PRIVATE "-Wl,--no-undefined")
endif()
if(ANDROID)
  find_library(ANDROID_LOG_LIBRARY log REQUIRED)
  target_link_libraries(native_pocket_resident PRIVATE ${ANDROID_LOG_LIBRARY})
endif()
