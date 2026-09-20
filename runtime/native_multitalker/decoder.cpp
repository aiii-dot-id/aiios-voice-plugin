#include "decoder.h"
#include <cmath>
#include <limits>

namespace aii::multitalker {
void Decoder::reset(uint64_t epoch) {
  if (!epoch || epoch <= epoch_) throw std::invalid_argument("decoder epoch must advance");
  backend_.reopen();
  tracks_ = {};
  epoch_ = epoch;
  faulted_ = false;
  cancelled_.store(false);
}
void Decoder::cancel() noexcept {
  cancelled_.store(true);
  backend_.cancel();
}
std::vector<Token> Decoder::push(uint64_t epoch, uint32_t track, uint64_t first,
                                 const float* encoded, size_t frames) {
  if (!epoch || epoch != epoch_) throw std::invalid_argument("stale decoder epoch");
  if (track >= track_count || !encoded || !frames || frames > 128 ||
      first > std::numeric_limits<uint64_t>::max() - frames)
    throw std::invalid_argument("decoder input extent");
  auto& t = tracks_[track];
  // Gaps are allowed: another speaker may have been active while this track
  // was idle. Reusing or reversing any frame is never allowed.
  if (t.seen && first < t.end) throw std::invalid_argument("decoder frame replay");
  if (faulted_ || cancelled_.load()) throw std::runtime_error("decoder epoch retired");
  for (size_t i = 0; i < frames * encoder_width; ++i)
    if (!std::isfinite(encoded[i])) throw std::invalid_argument("nonfinite encoder frame");
  std::vector<Token> out;
  try {
    for (size_t frame = 0; frame < frames; ++frame) {
      // Same ten-symbol-per-frame bound as the pinned reference. An exhausted
      // bound advances the frame; it does not fabricate blank or drop a token.
      for (size_t symbol = 0; symbol < 10; ++symbol) {
        if (cancelled_.load()) throw std::runtime_error("decoder cancelled");
        if (!t.predicted) {
          t.prediction = backend_.predict(t.last, t.state);
          t.predicted = true;
        }
        if (cancelled_.load()) throw std::runtime_error("decoder cancelled");
        const auto next = backend_.classify(encoded + frame * encoder_width, t.prediction);
        if (cancelled_.load()) throw std::runtime_error("decoder cancelled");
        if (next < 0 || next > blank_token) throw std::runtime_error("decoder token range");
        if (next == blank_token) break;
        out.push_back({next, first + frame});
        t.last = next;
        t.state = t.prediction.next;
        t.predicted = false;
      }
    }
    t.seen = true;
    t.end = first + frames;
    return out;
  } catch (...) {
    faulted_ = true;
    throw;
  }
}
}
