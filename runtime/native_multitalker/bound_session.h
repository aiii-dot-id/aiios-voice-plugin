#pragma once
#include <onnxruntime_cxx_api.h>
#include <filesystem>

namespace aii::multitalker {
Ort::Session bound_session(Ort::Env&, const std::string& root,
                           const char* model, const Ort::SessionOptions&);
}
