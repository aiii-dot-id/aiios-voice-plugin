#pragma once
#include <string_view>

// Bound by the platform runtime build, independently of TTS and ASR. Existing
// mobile builds keep their explicit selection; no environment fallback exists.
#if defined(AII_UID_NCNN) && defined(AII_MOBILE_COREML_CANDIDATE)
#error "Choose one explicit UID implementation"
#endif
#ifndef AII_SESSION_UID_BACKEND
#if defined(AII_UID_NCNN)
#define AII_SESSION_UID_BACKEND "ncnn-vulkan"
#elif defined(AII_MOBILE_COREML_CANDIDATE)
#define AII_SESSION_UID_BACKEND "coreml-ane"
#else
#define AII_SESSION_UID_BACKEND "cpu"
#endif
#endif

namespace aii::voice {
inline constexpr std::string_view uid_backend = AII_SESSION_UID_BACKEND;
static_assert(uid_backend == "cpu" || uid_backend == "cuda" ||
              uid_backend == "coreml-ane" || uid_backend == "ncnn-vulkan",
              "Unsupported bound UID backend");
#ifdef AII_UID_NCNN
static_assert(uid_backend == "ncnn-vulkan", "ncnn UID backend conflicts with runtime binding");
#endif
#ifdef AII_MOBILE_COREML_CANDIDATE
static_assert(uid_backend == "coreml-ane", "mobile CoreML UID conflicts with runtime binding");
#endif
inline constexpr const char* uid_provider = uid_backend == "cuda" ? "CUDAExecutionProvider" :
  uid_backend == "coreml-ane" ? "CoreMLExecutionProvider" :
  uid_backend == "ncnn-vulkan" ? "ncnn-vulkan" : "CPUExecutionProvider";
}
