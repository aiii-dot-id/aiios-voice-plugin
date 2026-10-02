cmake_minimum_required(VERSION 3.22)
project(aii_native_echo LANGUAGES C CXX)
include("${CMAKE_CURRENT_LIST_DIR}/../cmake/SourcePrivacy.cmake")
set(AII_AEC3_SOURCE "" CACHE PATH "Exact standalone AEC3 source checkout")
aii_map_source_path("${AII_AEC3_SOURCE}" "/aii-deps/webrtc-aec3")
find_package(Python3 REQUIRED COMPONENTS Interpreter)
execute_process(COMMAND "${Python3_EXECUTABLE}"
  "${CMAKE_CURRENT_LIST_DIR}/verify_dependency.py" "${AII_AEC3_SOURCE}"
  RESULT_VARIABLE verified)
if(NOT verified EQUAL 0)
  message(FATAL_ERROR "AEC3 source binding failed")
endif()
set(BUILD_TESTS OFF CACHE BOOL "Upstream demos are not acceptance tests" FORCE)
add_subdirectory("${AII_AEC3_SOURCE}" "${CMAKE_CURRENT_BINARY_DIR}/aec3")
if(MSVC)
  # The pinned upstream target unconditionally inherits GCC-only flags.
  # Adapt the target, not its verified source checkout, for the native compiler.
  get_target_property(aec_compile_options webrtc_aec3 COMPILE_OPTIONS)
  list(REMOVE_ITEM aec_compile_options -Wall -fPIC -Wno-deprecated -m64 -fexceptions)
  set_property(TARGET webrtc_aec3 PROPERTY COMPILE_OPTIONS "${aec_compile_options}")
  # The pinned source relies on transitive <array> inclusion on other STLs.
  target_compile_options(webrtc_aec3 PRIVATE /W4 /EHsc /bigobj)
  set_property(SOURCE "${AII_AEC3_SOURCE}/src/audio_processing/audio_buffer.cc"
    TARGET_DIRECTORY webrtc_aec3 APPEND PROPERTY COMPILE_OPTIONS /FIarray)
  target_compile_definitions(webrtc_aec3 PUBLIC NOMINMAX WIN32_LEAN_AND_MEAN
    _USE_MATH_DEFINES)
  set_target_properties(webrtc_aec3 PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON)
  target_link_libraries(webrtc_aec3 PRIVATE winmm)
endif()
# Upstream collects x86 dispatch implementations but omits their compilation
# flags. Apply AVX2/FMA only to those files, never to the baseline library.
if(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|AMD64|amd64")
  get_target_property(aec_sources webrtc_aec3 SOURCES)
  # Keep the verified upstream checkout pristine. Apply the auditable portable
  # register-access patch to one build-tree copy on every x86 compiler.
  set(aec_portable_dir "${CMAKE_CURRENT_BINARY_DIR}/aec3-portable")
  file(MAKE_DIRECTORY "${aec_portable_dir}")
  set(aec_matched_filter "${AII_AEC3_SOURCE}/src/audio_processing/aec3/matched_filter_avx2.cc")
  configure_file("${aec_matched_filter}"
    "${aec_portable_dir}/matched_filter_avx2.cc" COPYONLY)
  find_package(Git REQUIRED)
  execute_process(COMMAND "${CMAKE_COMMAND}" -E env
    "GIT_CEILING_DIRECTORIES=${CMAKE_CURRENT_BINARY_DIR}"
    "${GIT_EXECUTABLE}" apply
    "${CMAKE_CURRENT_SOURCE_DIR}/aec3-portable-vector-access.patch"
    WORKING_DIRECTORY "${aec_portable_dir}" RESULT_VARIABLE patched)
  if(NOT patched EQUAL 0)
    message(FATAL_ERROR "AEC3 portable vector-access patch failed")
  endif()
  file(SHA256 "${aec_portable_dir}/matched_filter_avx2.cc" patched_sha)
  if(NOT patched_sha STREQUAL "feac4f5cebbd2583a722514afccdf34aeabb038a2b28a43271e57878dd45843b")
    message(FATAL_ERROR "AEC3 portable vector-access bytes differ")
  endif()
  list(REMOVE_ITEM aec_sources "${aec_matched_filter}")
  list(APPEND aec_sources "${aec_portable_dir}/matched_filter_avx2.cc")
  set_property(TARGET webrtc_aec3 PROPERTY SOURCES "${aec_sources}")
  aii_map_source_path("${aec_portable_dir}" "/aii-deps/webrtc-aec3-portable")
  foreach(source IN LISTS aec_sources)
    if(source MATCHES "_avx2\\.cc$")
      if(MSVC)
        set_property(SOURCE "${source}" TARGET_DIRECTORY webrtc_aec3
          APPEND PROPERTY COMPILE_OPTIONS /arch:AVX2)
      else()
        set_property(SOURCE "${source}" TARGET_DIRECTORY webrtc_aec3
          APPEND PROPERTY COMPILE_OPTIONS -mavx2 -mfma)
      endif()
    endif()
  endforeach()
endif()
if(APPLE)
  target_link_libraries(webrtc_aec3 PRIVATE "-framework CoreFoundation")
elseif(UNIX)
  find_package(Threads REQUIRED)
  target_link_libraries(webrtc_aec3 PRIVATE Threads::Threads)
endif()
add_library(aii_native_echo SHARED echo.cpp)
target_compile_features(aii_native_echo PRIVATE cxx_std_20)
target_include_directories(aii_native_echo PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(aii_native_echo PRIVATE webrtc_aec3)
set_target_properties(aii_native_echo PROPERTIES
  CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN YES)
if(APPLE)
  set_target_properties(aii_native_echo PROPERTIES INSTALL_RPATH "@loader_path")
elseif(UNIX)
  set_target_properties(aii_native_echo PROPERTIES INSTALL_RPATH "$ORIGIN")
endif()
enable_testing()
add_test(NAME native_echo_dependency_binding COMMAND "${Python3_EXECUTABLE}"
  "${CMAKE_CURRENT_SOURCE_DIR}/verify_dependency_test.py")
add_executable(aii_echo_test echo_test.cpp)
target_compile_features(aii_echo_test PRIVATE cxx_std_17)
target_link_libraries(aii_echo_test PRIVATE aii_native_echo)
add_test(NAME native_echo_contracts COMMAND aii_echo_test)
add_executable(aii_echo_input_test input_test.cpp)
target_compile_features(aii_echo_input_test PRIVATE cxx_std_17)
target_link_libraries(aii_echo_input_test PRIVATE aii_native_echo)
add_test(NAME native_echo_input_contracts COMMAND aii_echo_input_test)
set_tests_properties(native_echo_contracts PROPERTIES TIMEOUT 30)
install(TARGETS aii_native_echo webrtc_aec3 LIBRARY DESTINATION lib RUNTIME DESTINATION bin)
install(FILES "${AII_AEC3_SOURCE}/LICENSE" DESTINATION licenses RENAME webrtc-aec3-LICENSE)
install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/NOTICE" DESTINATION licenses RENAME native-echo-NOTICE)
