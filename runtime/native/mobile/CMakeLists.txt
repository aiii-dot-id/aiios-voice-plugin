cmake_minimum_required(VERSION 3.22)
project(aii_voice_mobile LANGUAGES C CXX OBJC OBJCXX)
if(NOT CMAKE_SYSTEM_NAME STREQUAL "iOS")
  message(FATAL_ERROR "This app-linked recipe is for iOS; Android uses the shared native graph")
endif()
foreach(required ENGINE_SOURCE ORT_INCLUDE ORT_LIBRARY SLEEF_INCLUDE SLEEF_LIBRARY POCKETFFT_ROOT UID_FRONTEND_SOURCE UID_KNF_ROOT UID_KISS_ROOT)
  if(NOT EXISTS "${${required}}")
    message(FATAL_ERROR "Explicit bound ${required} required")
  endif()
endforeach()
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "App-linked native engine" FORCE)
add_compile_definitions(GGML_GELU_FORCE_F32)
# SentencePiece expects this helper from its optional ios.toolchain.cmake.
# Use CMake's native iOS toolchain and supply only the property helper, without
# importing a second toolchain that changes SDK, architecture or signing.
function(set_xcode_property target property value variant)
  if(NOT variant STREQUAL "All")
    message(FATAL_ERROR "Unexpected configuration-specific SentencePiece property")
  endif()
  set_target_properties(${target} PROPERTIES XCODE_ATTRIBUTE_${property} "${value}")
endfunction()
add_subdirectory("${ENGINE_SOURCE}" engine)
add_subdirectory("${UID_FRONTEND_SOURCE}" uid-frontend)
set(r "${CMAKE_CURRENT_LIST_DIR}/../..")
set(s "${r}/native/session")
# One internal C ABI and the same model/session implementation as desktop.
# No Python, process spawn, downloaded code, device capture or private SDK API.
add_library(aii_voice_mobile STATIC
  "${s}/session.cpp" "${s}/text.cpp" "${s}/c_api.cpp"
  "${s}/native_models.cpp" "${s}/native_c_api.cpp" "${s}/native_speaker.cpp"
  "${r}/native/platform/readonly_model.cpp" "${r}/native/vendor/cjson/cJSON.c"
  "${r}/native_asr/asr.cpp" "${r}/native_asr/frontend.cpp" "${r}/native_asr/initializers.cpp"
  "${r}/native_vad/vad.cpp" "${r}/native_uid/uid.cpp" "${r}/native_uid/identity.cpp"
  "${r}/native_uid/snapshot.cpp" "${r}/native_uid/enrollment.cpp"
  "${r}/native_endpoint/endpoint.cpp" "${r}/native_endpoint/frontend.cpp"
  "${r}/native_endpoint/math.cpp" "${r}/native_endpoint/fft.cpp"
  "${r}/native_pocket/resident.cpp" "${r}/native_pocket/assets_bound.cpp")
target_compile_definitions(aii_voice_mobile PRIVATE AII_WITH_UID AII_UID_BUILD AII_VAD_BUILD AII_ENDPOINT_BUILD)
option(AII_ASR_MOBILE_FLAT_ALLOCATOR "Explicit unqualified mobile allocation candidate" OFF)
option(AII_MOBILE_COREML_CANDIDATE "Explicit measured Core ML candidate, not a placement claim" OFF)
if(AII_MOBILE_COREML_CANDIDATE)
  target_compile_definitions(aii_voice_mobile PRIVATE AII_MOBILE_COREML_CANDIDATE)
endif()
if(AII_ASR_MOBILE_FLAT_ALLOCATOR)
  target_compile_definitions(aii_voice_mobile PRIVATE AII_ASR_MOBILE_FLAT_ALLOCATOR)
endif()
target_compile_options(aii_voice_mobile PRIVATE -Wall -Wextra -ffp-contract=off)
target_include_directories(aii_voice_mobile PRIVATE "${ORT_INCLUDE}" "${SLEEF_INCLUDE}"
  "${POCKETFFT_ROOT}" "${UID_FRONTEND_SOURCE}" "${r}/native/vendor/cjson"
  "${ENGINE_SOURCE}/include" "${ENGINE_SOURCE}/external/ggml/include"
  "${ENGINE_SOURCE}/src/models/pocket_tts")
target_link_libraries(aii_voice_mobile PUBLIC engine_runtime ggml aiii_uid_frontend
  "${ORT_LIBRARY}" "${SLEEF_LIBRARY}" "-framework Foundation"
  "-framework CoreML" "-framework Accelerate")
add_executable(AiiVoiceMobile MACOSX_BUNDLE ios_main.mm "${s}/c_api_probe.cpp" "${r}/native_pocket/android/probe.cpp")
set_source_files_properties("${s}/c_api_probe.cpp" PROPERTIES COMPILE_DEFINITIONS "main=aii_mobile_fixture_main")
set_source_files_properties("${r}/native_pocket/android/probe.cpp" PROPERTIES COMPILE_DEFINITIONS "main=aii_mobile_tts_fixture_main")
target_link_libraries(AiiVoiceMobile PRIVATE aii_voice_mobile "-framework UIKit" "-framework CoreGraphics")
set_target_properties(AiiVoiceMobile PROPERTIES
  MACOSX_BUNDLE_GUI_IDENTIFIER "id.aiii.voice.portability.full"
  MACOSX_BUNDLE_BUNDLE_NAME "AII Voice Mobile"
  MACOSX_BUNDLE_BUNDLE_VERSION "1"
  MACOSX_BUNDLE_SHORT_VERSION_STRING "0.1"
  MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_LIST_DIR}/Info.plist.in")
