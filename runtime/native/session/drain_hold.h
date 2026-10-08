#pragma once
#include "snapshot_bridge.h"
#include <algorithm>
#include <chrono>

namespace aii::voice {
// WORK WITH A LIMIT OF ITS OWN IS BOUNDED BY THAT LIMIT, NOT BY A DRAIN'S.
// A drain is called stalled when nothing has moved for its idle limit. Three
// things can be in flight that long, each inside its own time, without the
// core numbering one event:
//   an operation on the host's storage, a speaker's observation read and
//     published step by step, each step inside the limits storage has;
//   a model call, which the session's watchdog holds to model_call_ms;
//   a write of audio to the host, which the pipe's own deadline holds to
//     audio_write_ms.
// The drain called each of them "no progress", failed a session that was
// ending cleanly and blamed the wrong thing. This is the one rule for the
// three, from one look on every pass of the worker: the bridge's activity
// under its lock, the model calls in the core's status, and whether the
// worker has a write of audio in flight. Work in flight holds a deadline that
// has passed, so its own limit is the one that speaks. Work that has ended
// since the last look moved the drain when it ended: the deadline is the idle
// limit from this look, which is at most one pass after the work, and never
// sooner than it was. An abort waits for none of it.
//
// Audio waiting in the core's queue to be taken (output_take_ms) is held by
// the third: the worker is that queue's one consumer and takes whenever it
// has no write in flight, so audio stays untaken only behind such a write.
class DrainHold {
 public:
  using Clock = std::chrono::steady_clock;
  // What is in flight at this look. Of two, the one written first here.
  enum class By { nothing, storage, model_call, audio_write };
  // The core's model calls, from its status: whether its watchdog holds one
  // now and how many it has given back. A write of audio that ended is
  // counted where its acknowledgement is taken, so only its flag is read.
  struct ModelCalls { bool in_flight; uint64_t ended; };
  // `draining` is a drain that is not an abort. Outside one, what has ended
  // is counted and nothing else: it is not a later drain's progress.
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
} // namespace aii::voice
