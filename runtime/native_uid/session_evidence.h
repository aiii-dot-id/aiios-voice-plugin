#pragma once
#include "identity.h"
#include <chrono>
#include <map>
#include <mutex>
#include <set>
#include <stdexcept>

namespace aii::uid {
// Ephemeral evidence, not an enrollment store. Only the UID inference owner
// inserts the complete finalized span it actually embedded. Preparation copies
// selected vectors; it never invokes inference or locks an audio/control owner.
class SessionEvidence {
 public:
  using Clock=std::chrono::steady_clock;
  static constexpr size_t capacity=16;
  static constexpr auto lifetime=std::chrono::minutes(10);
  uint64_t begin() {
    std::lock_guard<std::mutex> lock(mutex_);
    if(epoch_==UINT64_MAX)throw std::runtime_error("UID evidence epoch exhausted");
    ++epoch_;live_=true;rows_.clear();return epoch_;
  }
  void cancel() noexcept {
    std::lock_guard<std::mutex> lock(mutex_);live_=false;rows_.clear();
  }
  uint64_t epoch() const {
    std::lock_guard<std::mutex> lock(mutex_);
    if(!live_)throw std::invalid_argument("UID evidence session unavailable");
    return epoch_;
  }
  void retain(uint64_t epoch,uint64_t final,Sample sample,Clock::time_point now=Clock::now()) {
    std::lock_guard<std::mutex> lock(mutex_);
    if(!live_||epoch!=epoch_||!final)throw std::invalid_argument("UID evidence session retired");
    prune(now);
    // Never replace what a previously named final means, even with equal bytes.
    if(rows_.count(final))throw std::invalid_argument("UID final evidence already retained");
    if(rows_.size()==capacity)rows_.erase(rows_.begin());
    rows_.emplace(final,Row{std::move(sample),now});
  }
  std::vector<Sample> select(uint64_t epoch,const std::vector<uint64_t>& finals,
                            Clock::time_point now=Clock::now()) {
    std::lock_guard<std::mutex> lock(mutex_);
    if(!live_||epoch!=epoch_)throw std::invalid_argument("UID evidence session retired");
    if(finals.empty()||finals.size()>8)throw std::invalid_argument("select one to eight finalized recordings");
    prune(now);std::set<uint64_t> seen;std::vector<Sample> result;
    for(auto final:finals) {
      if(!seen.insert(final).second)throw std::invalid_argument("duplicate selected final");
      auto row=rows_.find(final);
      if(row==rows_.end())throw std::invalid_argument("selected final evidence unavailable, expired or evicted");
      result.push_back(row->second.sample);
    }
    return result;
  }
  std::vector<uint64_t> available(Clock::time_point now=Clock::now()) {
    std::lock_guard<std::mutex> lock(mutex_);
    if(!live_)throw std::invalid_argument("UID evidence session unavailable");
    prune(now);std::vector<uint64_t> result;
    for(const auto& row:rows_)result.push_back(row.first);
    return result;
  }
 private:
  struct Row {Sample sample;Clock::time_point retained;};
  void prune(Clock::time_point now) {
    for(auto it=rows_.begin();it!=rows_.end();) {
      if(now-it->second.retained>=lifetime)it=rows_.erase(it);else ++it;
    }
  }
  mutable std::mutex mutex_;
  uint64_t epoch_=0;
  bool live_=false;
  std::map<uint64_t,Row> rows_;
};
}
