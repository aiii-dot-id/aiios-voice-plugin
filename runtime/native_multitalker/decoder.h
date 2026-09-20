#pragma once
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace aii::multitalker {
// Internal model composition, not a new SDK protocol. Anonymous acoustic tracks
// are not enrolled people. No enrollment labels or reference words enter here.
constexpr size_t track_count = 4, encoder_width = 1024, prediction_width = 640;
constexpr size_t recurrent_size = 2 * prediction_width;
constexpr int64_t blank_token = 1024;
struct State {
  std::array<float, recurrent_size> hidden{}, cell{};
};
struct Prediction {
  std::array<float, prediction_width> values{};
  State next;
};
struct Backend {
  virtual ~Backend() = default;
  virtual Prediction predict(int64_t token, const State&) = 0;
  virtual int64_t classify(const float* frame, const Prediction&) = 0;
  virtual void cancel() noexcept = 0;
  virtual void reopen() = 0; // owner only, after every inference caller retires
};
struct Token { int64_t id; uint64_t frame; };
class Decoder {
 public:
  explicit Decoder(Backend& backend) : backend_(backend) {}
  // One inference owner. Cancellation is the only concurrent call. A failed or
  // cancelled decode poisons this epoch; its partially consumed state is never
  // retried. Reset only after the inference owner retires.
  void reset(uint64_t epoch);
  std::vector<Token> push(uint64_t epoch, uint32_t track, uint64_t first_frame,
                          const float* encoded, size_t frames);
  void cancel() noexcept;
 private:
  struct Track {
    State state;
    Prediction prediction;
    int64_t last = blank_token;
    uint64_t end = 0;
    bool predicted = false, seen = false;
  };
  Backend& backend_;
  std::array<Track, track_count> tracks_{};
  uint64_t epoch_ = 0;
  bool faulted_ = false;
  std::atomic<bool> cancelled_{false};
};
}
