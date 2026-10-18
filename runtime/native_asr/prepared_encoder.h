#pragma once
#include "initializers.h"

namespace aii::asr {
struct PreparedBinding { Pin graph, weights; };
inline PreparedBinding prepared_binding() {
  return {{746856, "37a1bcb19e92dfacbffd5f18e2948d30412d88a277e9eb943399654f0069af5b"},
          {2495361024ULL, "cacfba092fa4b51d8adf6bf54c8295e11344e91ed124ac503e7bc3e2afc91808"}};
}
struct PreparedEncoder {
  aii::platform::ReadonlyModel graph, weights;
  explicit PreparedEncoder(const std::filesystem::path& root,
                           const PreparedBinding& pin = prepared_binding())
      : graph(root / "encoder.optimized.onnx", pin.graph.bytes, pin.graph.sha),
        weights(root / "weights.bin", pin.weights.bytes, pin.weights.sha) {}
  void attach(Ort::SessionOptions& options) {
    options.AddExternalInitializersFromFilesInMemory(
        {std::filesystem::path("weights.bin").native()},
        {const_cast<char*>(reinterpret_cast<const char*>(weights.data()))},
        {weights.size()});
  }
  void check_unchanged() const { graph.check_unchanged(); weights.check_unchanged(); }
};
}
