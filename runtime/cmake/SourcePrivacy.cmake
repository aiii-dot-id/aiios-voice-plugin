# Apply before add_subdirectory: dependency assertions also embed __FILE__.
# Do not alter numerical options or strip diagnostics to hide a build path.
include_guard(GLOBAL)
get_filename_component(_aii_repo "${CMAKE_CURRENT_LIST_DIR}/../.." REALPATH)
if(MSVC)
  # Without this switch MSVC warns D9007 and silently ignores every path map.
  add_compile_options("/experimental:deterministic")
  # Store debug data in objects: /Zi's compiler PDB path is itself remapped,
  # which can turn its output into an unwritable synthetic directory.
  add_compile_options("$<$<CONFIG:Debug,RelWithDebInfo>:/Z7>")
endif()

function(aii_map_source_path source replacement)
  if(NOT source)
    return()
  endif()
  get_filename_component(source "${source}" ABSOLUTE)
  get_filename_component(resolved "${source}" REALPATH)
  foreach(path IN ITEMS "${source}" "${resolved}")
    if(MSVC)
      file(TO_NATIVE_PATH "${path}" native_path)
      add_compile_options("/pathmap:${native_path}=${replacement}")
    elseif(CMAKE_CXX_COMPILER_ID MATCHES "^(GNU|Clang|AppleClang)$")
      add_compile_options("-ffile-prefix-map=${path}=${replacement}")
    else()
      message(FATAL_ERROR "Source-path privacy requires a qualified compiler")
    endif()
  endforeach()
endfunction()

aii_map_source_path("${_aii_repo}" "/aii-source")
aii_map_source_path("${CMAKE_SOURCE_DIR}" "/aii-source/entry")
aii_map_source_path("${CMAKE_BINARY_DIR}" "/aii-build")
foreach(name UID_FRONTEND_SOURCE UID_KNF_ROOT UID_KISS_ROOT ENGINE_SOURCE
             RESIDENT_SOURCE MIMI_OVERRIDE ACOUSTIC_OVERRIDE BOUND_SOURCE
             FLOW_OVERRIDE KV_OVERRIDE PATH_SHIM_SOURCE PROFILE_SESSION_SOURCE
             ORT_INCLUDE POCKETFFT_ROOT SLEEF_INCLUDE ATEN_ROOT)
  if(DEFINED ${name} AND EXISTS "${${name}}")
    set(path "${${name}}")
    if(NOT IS_DIRECTORY "${path}")
      get_filename_component(path "${path}" DIRECTORY)
    endif()
    aii_map_source_path("${path}" "/aii-deps/${name}")
  endif()
endforeach()
if(MSVC)
  # Keep any CodeView reference independent of a private linker output path.
  add_link_options("/PDBALTPATH:%_PDB%")
elseif(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options("-fdebug-compilation-dir=/aii-build")
endif()
if(APPLE)
  # Darwin's linker records object locations independently of compiler DWARF.
  # Retain the debug map with paths relative to the build directory.
  get_filename_component(_aii_build "${CMAKE_BINARY_DIR}" REALPATH)
  add_link_options("LINKER:-oso_prefix,${_aii_build}/")
endif()
