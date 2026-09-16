cmake_minimum_required(VERSION 3.19)
project(native_pocket_stream_probe LANGUAGES CXX)
if(NOT WIN32)
  message(FATAL_ERROR "This probe links the qualified native Windows libraries")
endif()
foreach(required ENGINE_SOURCE ENGINE_BUILD PROBE_SOURCE VULKAN_LIBRARY)
  if(NOT DEFINED ${required})
    message(FATAL_ERROR "Missing ${required}")
  endif()
endforeach()
add_executable(native_stream_probe "${PROBE_SOURCE}")
target_compile_features(native_stream_probe PRIVATE cxx_std_17)
target_compile_options(native_stream_probe PRIVATE /O2 /EHsc /openmp)
target_include_directories(native_stream_probe PRIVATE
  "${ENGINE_SOURCE}/include" "${ENGINE_SOURCE}/external/ggml/include")
if(DEFINED MIMI_OVERRIDE)
  # Private single-component falsifier: the remaining immutable native
  # libraries are identical to the offline-qualified build.
  target_sources(native_stream_probe PRIVATE "${MIMI_OVERRIDE}")
  target_include_directories(native_stream_probe PRIVATE "${ENGINE_SOURCE}/src/models/pocket_tts")
  target_compile_definitions(native_stream_probe PRIVATE GGML_GELU_FORCE_F32)
endif()
target_link_libraries(native_stream_probe PRIVATE
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
