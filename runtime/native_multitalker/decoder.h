#pragma once
#include <array>
#include "term_boost.h"
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <vector>

namespace aii::multitalker {
constexpr size_t track_count = 8, encoder_width = 1024, prediction_width = 640;
constexpr size_t dormant_track=track_count,state_count=track_count+1;
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
  virtual bool scores(const float* frame, const Prediction&, std::array<float, blank_token + 1>&) { (void)frame; return false; }
  virtual void cancel() noexcept = 0;
  virtual void reopen() = 0;
};
struct Token { int64_t id; uint64_t frame; };
class Decoder {
 public:
  explicit Decoder(Backend& backend) : backend_(backend) {}
  void reset(uint64_t epoch);
  void clone_track(uint64_t epoch,uint32_t source,uint32_t target);
  std::vector<Token> push(uint64_t epoch, uint32_t track, uint64_t first,
                          const float* encoded, size_t frames, bool final = false);
  std::vector<Token> finish(uint64_t epoch, uint32_t track);
  void cancel() noexcept;
  void boost(std::shared_ptr<const TermBoost> terms) { pending_ = std::move(terms); }
  static constexpr size_t match_frames = 32;
 private:
  struct Match {
    bool active = false, alive = false, spelled = false;
    State state;
    Prediction prediction;
    int64_t last = blank_token;
    bool predicted = false;
    uint64_t next = 0;
    size_t symbol = 0;
    float spent = 0;
    uint16_t term_words = 0;
    std::vector<std::pair<uint16_t, uint16_t>> at;
    std::vector<Token> pieces;
    std::vector<Token> held;
    uint16_t words = 0;
    size_t frames = 0;
  };
  struct Track {
    State state;
    Prediction prediction;
    int64_t last = blank_token;
    uint64_t end = 0;
    bool predicted = false, seen = false;
    Match match;
  };
  void frame(Track&, const float* encoded, uint64_t index, std::vector<Token>& out);
  void beside(Track&, const float* encoded, uint64_t index);
  bool word_ends(Track&, bool mark, std::vector<Token>& out);
  void settle(Track&, std::vector<Token>& out);
  Backend& backend_;
  std::array<Track, state_count> tracks_{};
  std::shared_ptr<const TermBoost> terms_, pending_;
  uint64_t epoch_ = 0;
  bool faulted_ = false;
  std::atomic<bool> cancelled_{false};
};
}
