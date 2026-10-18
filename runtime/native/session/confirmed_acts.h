#pragma once
#include <set>
#include <stdexcept>
#include <string>

namespace aii::voice {
class ConfirmedActs {
 public:
  void check(const std::string& id) const {
    if (id.empty() || id.size() > 128)
      throw std::invalid_argument("invalid operator act ID");
    if (id != "auto" && (used_.size() >= 1024 || used_.count(id)))
      throw std::invalid_argument("operator act already consumed or capacity exhausted");
  }
  void consume(const std::string& id) {
    check(id);
    if (id != "auto") used_.insert(id);
  }
 private:
  std::set<std::string> used_;
};
}
