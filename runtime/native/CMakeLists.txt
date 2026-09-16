cmake_minimum_required(VERSION 3.22)
project(aii_voice_native_components LANGUAGES C CXX)
enable_testing()

# One build graph for the components and private session owner. Full SDK,
# profile/UID integration and per-platform model linkage remain separate gates.
# Explicit dependency paths are supplied by the immutable platform recipe;
# configuration never downloads or discovers a system inference installation.
foreach(required ORT_INCLUDE ORT_LIBRARY UID_FRONTEND_SOURCE UID_KNF_ROOT UID_KISS_ROOT)
  if(NOT DEFINED ${required} OR NOT EXISTS "${${required}}")
    message(FATAL_ERROR "Existing, explicitly bound ${required} required")
  endif()
endforeach()

add_subdirectory(../native_asr asr)
add_subdirectory(../native_vad vad)
add_subdirectory(../native_uid uid)
add_subdirectory(../native_endpoint endpoint)
add_subdirectory(platform paths)
add_subdirectory(session)

add_custom_target(aii_voice_native_components ALL DEPENDS
  aii_native_asr aii_native_vad aii_native_uid aii_native_endpoint aii_native_session)

message(STATUS "Native components and private owner; no complete SDK session or accelerator qualification implied")
