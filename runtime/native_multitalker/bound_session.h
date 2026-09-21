#pragma once
#include <onnxruntime_cxx_api.h>
#include <filesystem>

namespace aii::multitalker {
// The Windows wall grants model files, not traversal of their ancestors.
// Resolve only our bound filenames and give ORT verified bytes, not paths.
Ort::Session bound_session(Ort::Env&, const std::string& root,
                           const char* model, const Ort::SessionOptions&);
}
