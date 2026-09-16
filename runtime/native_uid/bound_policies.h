#pragma once
#include "enrollment.h"
#include <optional>
#include <stdexcept>

namespace aii::uid {
// Only immutable, inventory-bound policy assets may enter this set. A profile
// can select one of them, never introduce its own thresholds or model binding.
// Reading an older profile does not rewrite it or change its matching rules.
class BoundPolicies {
 public:
  explicit BoundPolicies(const std::string& current, const std::string& previous = {})
      : current_(read_policy(current)) {
    if (!previous.empty()) {
      previous_ = read_policy(previous);
      // Reuse the transition's exact admission law without changing a profile.
      (void)prepare_guided_policy_transition(
          write_snapshot({previous_->policy, 0, {}}, *previous_), *previous_, current_);
    }
  }
  const PolicyDocument& current() const { return current_; }
  const std::optional<PolicyDocument>& previous() const { return previous_; }
  const PolicyDocument& resolve(const std::string& bytes) const {
    for (const auto* policy : {&current_, previous_ ? &*previous_ : nullptr}) {
      if (!policy) continue;
      const auto prefix = "{\"policy\":" + policy->canonical + ",\"revision\":";
      if (bytes.compare(0, prefix.size(), prefix) == 0) return *policy;
    }
    throw std::invalid_argument("enrollment policy is not bound by this runtime; no implicit upgrade");
  }
  Snapshot read(const std::string& bytes) const { return read_snapshot(bytes, resolve(bytes)); }
 private:
  PolicyDocument current_;
  std::optional<PolicyDocument> previous_;
};
}
