#pragma once
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>

namespace aii::platform {
std::string sha256(const void *data, size_t bytes);
std::string portable_sha256(const void *data, size_t bytes);
// A caller pins the parent directory for this activation. We open only its
// explicitly named regular file, never canonicalize an ONNX-supplied path.
// The same opened object is sized, hashed, mapped and checked after binding.
class ReadonlyModel {
public:
  ReadonlyModel(const std::filesystem::path &, uint64_t bytes,
                const std::string &sha);
  ~ReadonlyModel();
  ReadonlyModel(const ReadonlyModel &) = delete;
  const unsigned char *data() const noexcept;
  size_t size() const noexcept;
  void check_unchanged() const;

private:
  struct State;
  std::unique_ptr<State> state_;
};
} // namespace aii::platform
