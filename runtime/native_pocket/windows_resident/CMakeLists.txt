cmake_minimum_required(VERSION 3.19)
project(native_pocket_resident LANGUAGES CXX)
if(NOT WIN32)
  message(FATAL_ERROR "This private build links the measured Windows native libraries")
endif()
foreach(required ENGINE_SOURCE ENGINE_BUILD RESIDENT_SOURCE MIMI_OVERRIDE ACOUSTIC_OVERRIDE BOUND_SOURCE VULKAN_LIBRARY)
  if(NOT DEFINED ${required})
    message(FATAL_ERROR "Missing ${required}")
  endif()
endforeach()
add_library(native_pocket_resident SHARED "${RESIDENT_SOURCE}" "${MIMI_OVERRIDE}" "${ACOUSTIC_OVERRIDE}" "${BOUND_SOURCE}")
if(DEFINED FLOW_OVERRIDE)
  target_sources(native_pocket_resident PRIVATE "${FLOW_OVERRIDE}")
endif()
if(DEFINED KV_OVERRIDE)
  target_sources(native_pocket_resident PRIVATE "${KV_OVERRIDE}")
endif()
if(DEFINED PATH_SHIM_SOURCE)
  foreach(name resource_bundle.cpp filesystem.cpp safetensors.cpp paths.h)
    if(NOT EXISTS "${PATH_SHIM_SOURCE}/${name}")
      message(FATAL_ERROR "Incomplete explicitly bound native path shim: ${name}")
    endif()
  endforeach()
  target_sources(native_pocket_resident PRIVATE "${PATH_SHIM_SOURCE}/resource_bundle.cpp"
    "${PATH_SHIM_SOURCE}/filesystem.cpp" "${PATH_SHIM_SOURCE}/safetensors.cpp")
  target_include_directories(native_pocket_resident PRIVATE "${PATH_SHIM_SOURCE}")
endif()
if(DEFINED PROFILE_SESSION_SOURCE)
  get_filename_component(PROFILE_SOURCE_DIR "${PROFILE_SESSION_SOURCE}" DIRECTORY)
  target_sources(native_pocket_resident PRIVATE "${PROFILE_SESSION_SOURCE}" "${PROFILE_SOURCE_DIR}/profiling.cpp")
  target_include_directories(native_pocket_resident PRIVATE "${PROFILE_SOURCE_DIR}")
endif()
target_compile_features(native_pocket_resident PRIVATE cxx_std_17)
target_compile_options(native_pocket_resident PRIVATE /O2 /EHsc /openmp)
target_compile_definitions(native_pocket_resident PRIVATE GGML_GELU_FORCE_F32)
target_include_directories(native_pocket_resident PRIVATE
  "${ENGINE_SOURCE}/include" "${ENGINE_SOURCE}/external/ggml/include"
  "${ENGINE_SOURCE}/src/models/pocket_tts")
target_link_libraries(native_pocket_resident PRIVATE
  "${ENGINE_BUILD}/Release/engine_runtime.lib"
  "${ENGINE_BUILD}/ggml/src/Release/ggml.lib"
  "${ENGINE_BUILD}/ggml/src/Release/ggml-cpu.lib"
  "${ENGINE_BUILD}/ggml/src/ggml-vulkan/Release/ggml-vulkan.lib"
  "${ENGINE_BUILD}/ggml/src/Release/ggml-base.lib"
  "${VULKAN_LIBRARY}"
  "${ENGINE_BUILD}/external/sentencepiece/src/Release/sentencepiece.lib"
  "${ENGINE_BUILD}/Release/cjson_vendor.lib"
  "${ENGINE_BUILD}/Release/yaml_vendor.lib"
  kernel32 user32 gdi32 winspool shell32 ole32 oleaut32 uuid comdlg32 advapi32)
