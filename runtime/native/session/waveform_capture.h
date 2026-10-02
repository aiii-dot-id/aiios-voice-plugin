#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <deque>
#include <stdexcept>
#include <string>
#include <vector>

namespace aii::voice {

// Only admitted microphone PCM enters this volatile, bounded history. The
// record tool saves the last buffered signal; STT has no vote in that decision.
class RecentWaveforms {
 public:
  static constexpr uint32_t rate = 16000;
  static constexpr size_t window = 30 * rate;

  struct Recording {
    std::string session;
    uint64_t start = 0, end = 0;
    std::vector<int16_t> pcm;
  };

  ~RecentWaveforms() { clear(); }
  void begin(const std::string& session) {
    retain_window();
    session_ = session;
  }
  void end() { retain_window(); session_.clear(); }
  void clear() {
    end();
    wipe(last_.pcm);
    last_ = {};
  }
  void feed(uint64_t position, const float* input, size_t count) {
    if (!count) return;
    if (!input || session_.empty() || position != end_)
      throw std::invalid_argument("waveform input clock/session differs");
    for (size_t i = 0; i < count; ++i) {
      const double value = input[i];
      if (!std::isfinite(value) || value < -1. || value > 1.)
        throw std::invalid_argument("waveform PCM range differs");
      const auto scaled = std::nearbyint(std::clamp(value * 32768., -32768., 32767.));
      ring_.push_back(static_cast<int16_t>(scaled));
    }
    end_ += count;
    while (ring_.size() > window) { ring_.pop_front(); ++start_; }
  }
  Recording take_latest() {
    if (!available())
      throw std::invalid_argument("no buffered microphone audio to record");
    Recording chosen;
    if (!ring_.empty()) {
      chosen = {session_, start_, end_, {ring_.begin(), ring_.end()}};
      clear_window();
    } else {
      chosen = std::move(last_);
      last_ = {};
    }
    // The selected buffer is single-use; an older session cannot be replayed.
    wipe(last_.pcm);
    last_ = {};
    return chosen;
  }
  Recording latest() const {
    if (!available())
      throw std::invalid_argument("no buffered microphone audio to record");
    if (!ring_.empty())
      return {session_, start_, end_, {ring_.begin(), ring_.end()}};
    return last_;
  }
  // Recording publication is fallible. Retire only audio covered by a
  // verified save receipt; newer samples remain available for another call.
  void consume_saved(const std::string& session, uint64_t end) {
    if (session_ == session && end >= start_ && end <= end_) {
      while (!ring_.empty() && start_ < end) {
        volatile int16_t* p = &ring_.front();
        *p = 0;
        ring_.pop_front();
        ++start_;
      }
      wipe(last_.pcm);
      last_ = {};
    } else if (last_.session == session && last_.end == end) {
      wipe(last_.pcm);
      last_ = {};
    }
  }
  bool available() const { return !ring_.empty() || !last_.pcm.empty(); }
  static std::string wav(const std::vector<int16_t>& pcm) {
    if (pcm.empty() || pcm.size() > window)
      throw std::invalid_argument("bounded microphone waveform required");
    const uint32_t bytes = uint32_t(pcm.size() * 2);
    std::string out(44 + bytes, '\0');
    auto put16 = [&](size_t at, uint16_t n) {
      out[at] = char(n & 255); out[at+1] = char(n >> 8);
    };
    auto put32 = [&](size_t at, uint32_t n) {
      put16(at, uint16_t(n)); put16(at+2, uint16_t(n >> 16));
    };
    out.replace(0, 4, "RIFF"); put32(4, bytes + 36);
    out.replace(8, 4, "WAVE"); out.replace(12, 4, "fmt ");
    put32(16, 16); put16(20, 1); put16(22, 1); put32(24, rate);
    put32(28, rate * 2); put16(32, 2); put16(34, 16);
    out.replace(36, 4, "data"); put32(40, bytes);
    for (size_t i = 0; i < pcm.size(); ++i) put16(44 + i*2, uint16_t(pcm[i]));
    return out;
  }
  static void wipe(std::vector<int16_t>& pcm) noexcept {
    volatile int16_t* p = pcm.data();
    for (size_t i = 0; i < pcm.size(); ++i) p[i] = 0;
    std::vector<int16_t>().swap(pcm);
  }

 private:
  void clear_window() { std::fill(ring_.begin(), ring_.end(), int16_t{0}); ring_.clear(); start_ = end_; }
  void retain_window() {
    if (!ring_.empty()) {
      wipe(last_.pcm);
      last_ = {session_, start_, end_, {ring_.begin(), ring_.end()}};
    }
    clear_window();
    start_ = end_ = 0;
  }
  std::string session_;
  std::deque<int16_t> ring_;
  uint64_t start_ = 0, end_ = 0;
  Recording last_;
};
}
