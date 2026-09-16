#include "initializers.h"
#include "../native/vendor/cjson/cJSON.h"
#include <cmath>
#include <cstring>
#include <set>
#include <stdexcept>
namespace aii::asr {
namespace {
using Json = std::unique_ptr<cJSON, decltype(&cJSON_Delete)>;
void require(bool b, const char *s) {
  if (!b)
    throw std::runtime_error(s);
}
const cJSON *field(const cJSON *j, const char *key) {
  const auto *v = cJSON_GetObjectItemCaseSensitive(j, key);
  require(v, "missing external initializer field");
  return v;
}
std::string text(const cJSON *j) {
  require(cJSON_IsString(j) && j->valuestring,
          "external initializer text required");
  const std::string s = j->valuestring;
  require(!s.empty() && s.size() <= 1024, "external initializer name bound");
  return s;
}
uint64_t integer(const cJSON *j) {
  require(cJSON_IsNumber(j) && std::isfinite(j->valuedouble) &&
              j->valuedouble >= 0 && j->valuedouble <= 9007199254740991.0 &&
              std::floor(j->valuedouble) == j->valuedouble,
          "external initializer whole number required");
  return uint64_t(j->valuedouble);
}
void validate(const cJSON *j, size_t depth, size_t &count) {
  require(depth <= 16 && ++count <= 20000,
          "external initializer metadata structure bound");
  std::set<std::string> keys;
  for (const auto *p = j->child; p; p = p->next) {
    if (cJSON_IsObject(j))
      require(p->string && keys.insert(p->string).second,
              "duplicate external initializer metadata key");
    validate(p, depth + 1, count);
  }
}
Json parse(const unsigned char *p, size_t n) {
  require(n && n <= 4 * 1024 * 1024,
          "external initializer metadata byte bound");
  std::string s(reinterpret_cast<const char *>(p), n);
  require(s.find('\0') == std::string::npos &&
              s.find("\\u0000") == std::string::npos,
          "NUL external initializer metadata");
  Json j(cJSON_ParseWithLengthOpts(s.c_str(), s.size() + 1, nullptr, 1),
         cJSON_Delete);
  require(bool(j) && cJSON_IsObject(j.get()),
          "external initializer JSON object required");
  size_t count = 0;
  validate(j.get(), 0, count);
  return j;
}
} // namespace
const Binding &encoder_binding() {
  static const Binding b{
      {42281004,
       "8b136db725b3c10b68a106482f0e83bf6a1136570959fe437cb8632222f7c3d7"},
      {197045,
       "b5373e14f6faeee3a405f8b13e5742ed5156b12bf294f85d61e9aada5bfd9a76"},
      {2552062944ULL,
       "9eebdd6590289cb3030f310858f3df93256600a800a3e8200c5993d5f967e174"},
      640,
      219};
  return b;
}
const Pin &decoder_binding() {
  static const Pin p{
      59764944,
      "f9c59ee6fa130bc2ba349dbcbba7c74a4a960a98d4196295ba4e8e5d6bde6b68"};
  return p;
}
const Pin &joiner_binding() {
  static const Pin p{
      37824291,
      "a6bd74c0a31cbde0da0368c1e29d171752108c7ad1231630e079a7d97ceee6f0"};
  return p;
}
const Pin &tokens_binding() {
  static const Pin p{
      131440,
      "729cc103155bafa785f9cd45746cd41cabe97eab7182fc04d594129587958f8a"};
  return p;
}
Initializers::Initializers(const std::filesystem::path &root,
                           const Binding &binding)
    : encoder(root / "encoder.onnx", binding.encoder.bytes,
              binding.encoder.sha),
      index(root / "weight-view.json", binding.index.bytes, binding.index.sha),
      checkpoint(root / "model.safetensors", binding.checkpoint.bytes,
                 binding.checkpoint.sha) {
  const uint16_t one = 1;
  require(*reinterpret_cast<const unsigned char *>(&one) == 1 &&
              sizeof(float) == 4,
          "little-endian float32 required");
  require(checkpoint.size() >= 8,
          "external initializer checkpoint header absent");
  uint64_t header_bytes = 0;
  for (unsigned i = 0; i < 8; ++i)
    header_bytes |= uint64_t(checkpoint.data()[i]) << (8 * i);
  require(header_bytes >= 2 && header_bytes <= 4 * 1024 * 1024 &&
              header_bytes < checkpoint.size() - 8,
          "external initializer header extent");
  auto header = parse(checkpoint.data() + 8, size_t(header_bytes)),
       idx = parse(index.data(), index.size());
  require(text(field(idx.get(), "checkpoint_sha256")) ==
                  binding.checkpoint.sha &&
              integer(field(idx.get(), "checkpoint_bytes")) ==
                  binding.checkpoint.bytes,
          "external initializer checkpoint binding differs");
  const auto *records = field(idx.get(), "initializers");
  require(cJSON_IsArray(records) &&
              size_t(cJSON_GetArraySize(records)) == binding.count,
          "external initializer census differs");
  std::set<std::string> seen_names, seen_refs;
  names.reserve(binding.count);
  values.reserve(binding.count);
  const auto memory =
      Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
  for (const auto *row = records->child; row; row = row->next) {
    require(cJSON_IsObject(row), "external initializer row required");
    auto name = text(field(row, "initializer"));
    const auto ref = text(field(row, "reference")),
               transform = text(field(row, "transform"));
    if (transform == "transpose") {
      name += "/reference_storage";
      ++transposes;
    } else
      require(transform == "identity",
              "unproved external initializer transform");
    require(seen_names.insert(name).second && seen_refs.insert(ref).second,
            "duplicate external initializer identity");
    const auto *record = field(header.get(), ref.c_str());
    require(text(field(record, "dtype")) == "F32",
            "external initializer dtype differs");
    const auto *dims = field(record, "shape");
    require(cJSON_IsArray(dims) && cJSON_GetArraySize(dims) > 0 &&
                cJSON_GetArraySize(dims) <= 16,
            "external initializer shape required");
    std::vector<int64_t> shape;
    uint64_t elements = 1;
    for (const auto *d = dims->child; d; d = d->next) {
      const auto dim = integer(d);
      require(dim && dim <= binding.checkpoint.bytes / 4 &&
                  elements <= binding.checkpoint.bytes / 4 / dim,
              "external initializer shape overflow");
      elements *= dim;
      shape.push_back(int64_t(dim));
    }
    const auto *offsets = field(record, "data_offsets");
    require(cJSON_IsArray(offsets) && cJSON_GetArraySize(offsets) == 2,
            "external initializer offsets required");
    const auto first = integer(offsets->child),
               last = integer(offsets->child->next);
    const auto base = 8 + header_bytes;
    require(first <= checkpoint.size() - base && last >= first &&
                last <= checkpoint.size() - base &&
                last - first == elements * 4,
            "external initializer span exceeds checkpoint");
    const auto offset = base + first, bytes = last - first;
    require(offset == integer(field(row, "offset")) &&
                bytes == integer(field(row, "bytes")) &&
                offset % alignof(float) == 0,
            "external initializer span/layout differs");
    auto *raw = const_cast<float *>(
        reinterpret_cast<const float *>(checkpoint.data() + offset));
    values.push_back(Ort::Value::CreateTensor<float>(
        memory, raw, size_t(elements), shape.data(), shape.size()));
    names.push_back(std::move(name));
  }
  require(transposes == binding.transposes,
          "external initializer transpose census differs");
  check_unchanged();
}
void Initializers::attach(Ort::SessionOptions &options) {
  options.AddExternalInitializers(names, values);
}
void Initializers::check_unchanged() const {
  encoder.check_unchanged();
  index.check_unchanged();
  checkpoint.check_unchanged();
}
} // namespace aii::asr
