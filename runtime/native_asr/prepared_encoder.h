#pragma once
#include "initializers.h"

namespace aii::asr {
struct PreparedBinding { Pin graph, weights; };
inline PreparedBinding prepared_binding() {
  return {{746856, "37a1bcb19e92dfacbffd5f18e2948d30412d88a277e9eb943399654f0069af5b"},
          {2495361024ULL, "cacfba092fa4b51d8adf6bf54c8295e11344e91ed124ac503e7bc3e2afc91808"}};
}
// Private candidate artifact: these are its actual runtime inputs, not a
// discovered cache. Provenance to the original checkpoint belongs to the
// sealed offline derivation. Every runtime byte remains independently pinned.
// SessionOptions must die before this object; ORT copies the initializers.
struct PreparedEncoder {
  aii::platform::ReadonlyModel graph, weights;
  explicit PreparedEncoder(const std::filesystem::path& root,
                           const PreparedBinding& pin = prepared_binding())
      : graph(root / "encoder.optimized.onnx", pin.graph.bytes, pin.graph.sha),
        weights(root / "weights.bin", pin.weights.bytes, pin.weights.sha) {}
  void attach(Ort::SessionOptions& options) {
    // The ONNX location is resolved against this verified open mapping, never
    // used to open an ONNX-authored path. The pinned graph declares this name.
    options.AddExternalInitializersFromFilesInMemory(
        {std::filesystem::path("weights.bin").native()},
        {const_cast<char*>(reinterpret_cast<const char*>(weights.data()))},
        {weights.size()});
  }
  void check_unchanged() const { graph.check_unchanged(); weights.check_unchanged(); }
};
}
