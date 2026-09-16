cmake_minimum_required(VERSION 3.18)
project(aiii_uid_frontend LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
option(BUILD_SHARED_LIBS "Shared library for native validation" ON)
if(NOT UID_KNF_ROOT OR NOT UID_KISS_ROOT)
  message(FATAL_ERROR "Explicit verified Kaldi 1.22.3 and pinned KissFFT roots required")
endif()
set(knf "${UID_KNF_ROOT}/kaldi-native-fbank/csrc")
add_library(aiii_uid_frontend uid_frontend.cc
  "${knf}/feature-fbank.cc" "${knf}/feature-functions.cc"
  "${knf}/feature-window.cc" "${knf}/kaldi-math.cc"
  "${knf}/mel-computations.cc" "${knf}/rfft.cc" "${knf}/log.cc"
  "${UID_KISS_ROOT}/kiss_fft.c" "${UID_KISS_ROOT}/kiss_fftr.c")
target_include_directories(aiii_uid_frontend PRIVATE "${UID_KNF_ROOT}" "${UID_KISS_ROOT}")
target_compile_definitions(aiii_uid_frontend PRIVATE KNF_ENABLE_CHECK=1)
if(MSVC)
  set_target_properties(aiii_uid_frontend PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON)
else()
  target_compile_options(aiii_uid_frontend PRIVATE -fno-fast-math -ffp-contract=on)
endif()
