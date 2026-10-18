#pragma once
#include <cstddef>
#include <string_view>
namespace aii::uid {
inline constexpr char ecapa_binding[] =
    "67f55477cc9da7029cad6032cbb49e52ae7b6194dc4629b00d23facf3b741502";
inline constexpr char original_ecapa_binding[] =
    "ffe5c3d33b41fef374cc147df0061adc25e56ef35b44ed53977e906dac4e29f1";
inline size_t embedding_dimensions(std::string_view binding) {
  return binding == ecapa_binding || binding == original_ecapa_binding ? 192 : 256;
}
}
