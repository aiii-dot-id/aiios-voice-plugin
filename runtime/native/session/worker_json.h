#pragma once
#include "cJSON.h"
#include <cstdint>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
namespace aii::voice::wire {
struct Refused : std::runtime_error {
  using std::runtime_error::runtime_error;
};
using Json = std::unique_ptr<cJSON, decltype(&cJSON_Delete)>;
inline Json own(cJSON *p) {
  if (!p)
    throw std::bad_alloc();
  return Json(p, cJSON_Delete);
}
inline Json object() { return own(cJSON_CreateObject()); }
inline Json string(const std::string &s) {
  return own(cJSON_CreateString(s.c_str()));
}
inline Json number(uint64_t n) {
  if (n > 9007199254740991ULL)
    throw Refused("integer exceeded exact JSON range");
  return own(cJSON_CreateNumber(double(n)));
}
inline Json boolean(bool b) { return own(cJSON_CreateBool(b)); }
inline Json null() { return own(cJSON_CreateNull()); }
inline void put(Json &j, const char *key, Json value) {
  if (!cJSON_AddItemToObject(j.get(), key, value.get()))
    throw std::bad_alloc();
  value.release();
}
inline const cJSON *field(const cJSON *j, const char *key) {
  return cJSON_GetObjectItemCaseSensitive(j, key);
}
inline void require(bool b, const char *text) {
  if (!b)
    throw Refused(text);
}
inline std::string str(const cJSON *j, size_t limit = 256) {
  require(cJSON_IsString(j) && j->valuestring, "string required");
  std::string s(j->valuestring);
  require(!s.empty() && s.size() <= limit, "nonempty bounded string required");
  return s;
}
inline uint64_t integer(const cJSON *j, uint64_t max = 9007199254740991ULL) {
  require(cJSON_IsNumber(j) && std::isfinite(j->valuedouble) &&
              j->valuedouble >= 0 &&
              std::floor(j->valuedouble) == j->valuedouble &&
              j->valuedouble <= double(max),
          "bounded whole number required");
  return uint64_t(j->valuedouble);
}
inline bool flag(const cJSON *j) {
  require(cJSON_IsBool(j), "boolean required");
  return cJSON_IsTrue(j);
}
inline Json clone(const cJSON *j) {
  return j ? own(cJSON_Duplicate(j, 1)) : null();
}
inline std::string encode(const Json &j) {
  char *s = cJSON_PrintUnformatted(j.get());
  if (!s)
    throw std::bad_alloc();
  std::unique_ptr<char, decltype(&cJSON_free)> p(s, cJSON_free);
  return std::string(s);
}
inline void validate(const cJSON *j, unsigned depth, size_t &count) {
  require(depth <= 32 && ++count <= 10000, "JSON structure exceeded bound");
  std::set<std::string> keys;
  for (auto *child = j->child; child; child = child->next) {
    if (cJSON_IsObject(j))
      require(child->string && keys.insert(child->string).second,
              "duplicate JSON field");
    validate(child, depth + 1, count);
  }
}
inline Json parse(const std::string &s) {
  // cJSON strings cannot retain embedded NUL. Refuse that escape explicitly
  // rather than silently shortening IDs, text, or paths at the C boundary.
  require(s.find('\0') == std::string::npos,"NUL is not supported");
  for(size_t i=0;i<s.size();++i)if(s[i]=='\\') {
    require(s.compare(i,6,"\\u0000")!=0,"NUL escape is not supported");
    // Skip the escaped character. A literal backslash followed by u0000 is
    // valid text, not a NUL; refusing it silently excluded valid profile labels.
    if(i+1<s.size())++i;
  }
  const char *end = nullptr;
  auto *parsed = cJSON_ParseWithLengthOpts(s.c_str(), s.size() + 1, &end, 1);
  require(parsed != nullptr, "malformed private JSON");
  auto j = own(parsed);
  size_t count = 0;
  validate(j.get(), 0, count);
  return j;
}
} // namespace aii::voice::wire
