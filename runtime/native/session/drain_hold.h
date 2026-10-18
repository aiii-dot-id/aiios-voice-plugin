#pragma once
#include "snapshot_bridge.h"
#include <algorithm>
#include <chrono>

namespace aii::voice {
class DrainHold {
 public:
  using Clock = std::chrono::steady_clock;
  enum class By { nothing, storage, model_call, audio_write };
  struct ModelCalls { bool in_flight; uint64_t ended; };
  By look(const SnapshotBridge::Activity& storage, const ModelCalls& models, bool writing_audio, bool draining,
          Clock::time_point at, std::chrono::milliseconds idle, Clock::time_point& deadline) {
    const bool ended = storage.completed != storage_seen_ || models.ended != models_seen_;
    storage_seen_ = storage.completed;
    models_seen_ = models.ended;
    if (!draining)
      return By::nothing;
    if (ended)
      deadline = std::max(deadline, at + idle);
    return storage.busy ? By::storage : models.in_flight ? By::model_call : writing_audio ? By::audio_write : By::nothing;
  }

 private:
  uint64_t storage_seen_ = 0, models_seen_ = 0;
};
}
