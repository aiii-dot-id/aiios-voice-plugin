#pragma once
#include "../native/platform/readonly_model.h"
#include "onnxruntime_cxx_api.h"
#include <memory>
#include <vector>
namespace aii::asr {
struct Pin {
  uint64_t bytes;
  std::string sha;
};
struct Binding {
  Pin encoder, index, checkpoint;
  size_t count, transposes;
};
const Binding &encoder_binding();
const Pin &decoder_binding();
const Pin &joiner_binding();
const Pin &tokens_binding();
// Session construction copies external tensors into ORT's optimized model.
// Keep the original storage/values alive through construction and the final
// integrity check. Destroy the referring SessionOptions, then release these
// mappings; the constructed session owns its copy (AddExternalInitializers).
struct Initializers {
  aii::platform::ReadonlyModel encoder, index, checkpoint;
  std::vector<std::string> names;
  std::vector<Ort::Value> values;
  size_t transposes = 0;
  explicit Initializers(const std::filesystem::path &,
                        const Binding & = encoder_binding());
  void attach(Ort::SessionOptions &);
  void check_unchanged() const;
};
} // namespace aii::asr
