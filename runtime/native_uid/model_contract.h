#pragma once
#include <array>
#include <cstddef>
#include <stdexcept>
#include <string>

namespace aii::uid {
struct ModelContract {
  const char* embedding_binding;
  const char* sha256;
  size_t bytes;
};
// Runtime profiles may select only these exact numerical spaces. Adding a
// checkpoint never makes a previous speaker enrollment compatible with it.
inline constexpr std::array<ModelContract, 2> model_contracts{{
  {"c61bbdf12d5b69632b12776c23edc2e7155a9e0a028576eb41e509ca2bfb6d6e",
   "33af8affe6191b1ebd196d2b56e22c2934104cd2764abfdbdd954d3a934eb2a1", 100865597},
  // SHA256 of canonical configs/uid-voxceleb-resnet152-lm-binding.json.
  {"2575d15495d0e1cf17a2a12b0163dec0639db6218b3fe810e70369002d3898da",
   "5b734353b4b410e222bbd124dd095537642237ad895727d18a3b9fee330262a8", 79158228}
}};
inline const ModelContract& model_contract(const std::string& binding) {
  for (const auto& model : model_contracts)
    if (binding == model.embedding_binding) return model;
  throw std::invalid_argument("policy does not bind a supported native UID frontend/model");
}
inline bool model_extent_supported(size_t bytes) {
  for (const auto& model : model_contracts) if (bytes == model.bytes) return true;
  return false;
}
inline void verify_model(const ModelContract& model, size_t bytes, const std::string& sha256) {
  if (bytes != model.bytes || sha256 != model.sha256)
    throw std::invalid_argument("bound UID model hash/size differs");
}
struct NcnnContract {
  const char* embedding_binding;
  const char* source_sha256;
  const char* graph_sha256;
  size_t graph_bytes;
  const char* weight_sha256;
  size_t weight_bytes;
};
inline constexpr NcnnContract ncnn_contract{
  "2575d15495d0e1cf17a2a12b0163dec0639db6218b3fe810e70369002d3898da",
  "5b734353b4b410e222bbd124dd095537642237ad895727d18a3b9fee330262a8",
  "fbe949f7492a6f6e35a0d631b32b3cb7c759d83cac5ab9f3e5fd0e81be732703",29451,
  "c4daa4f2041e0ff54669c3fd66a1a5024eb5488ffb47ecebc68e587920d578db",79109744};
inline void verify_ncnn(const ModelContract& model, size_t graph_bytes,
    const std::string& graph_hash,size_t weight_bytes,const std::string& weight_hash) {
  if(std::string(model.embedding_binding)!=ncnn_contract.embedding_binding ||
     std::string(model.sha256)!=ncnn_contract.source_sha256 ||
     graph_bytes!=ncnn_contract.graph_bytes || graph_hash!=ncnn_contract.graph_sha256 ||
     weight_bytes!=ncnn_contract.weight_bytes || weight_hash!=ncnn_contract.weight_sha256)
    throw std::invalid_argument("native UID representation/model binding differs");
}
} // namespace aii::uid
