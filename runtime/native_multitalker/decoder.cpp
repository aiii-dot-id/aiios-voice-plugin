#include "decoder.h"
#include <algorithm>
#include <cmath>
#include <limits>

namespace aii::multitalker {
void Decoder::reset(uint64_t epoch) {
  if (!epoch || epoch <= epoch_) throw std::invalid_argument("decoder epoch must advance");
  backend_.reopen();
  tracks_ = {};
  terms_ = pending_ && !pending_->empty() ? pending_ : nullptr;
  epoch_ = epoch;
  faulted_ = false;
  cancelled_.store(false);
}
void Decoder::cancel() noexcept {
  cancelled_.store(true);
  backend_.cancel();
}
void Decoder::clone_track(uint64_t epoch,uint32_t source,uint32_t target) {
  if(!epoch || epoch!=epoch_ || source>=state_count || target>=track_count || source==target || tracks_[target].seen)
    throw std::invalid_argument("decoder clone lifecycle");
  if(faulted_ || cancelled_.load())throw std::runtime_error("decoder epoch retired");
  tracks_[target]=tracks_[source];
}
// The second path over one frame: the term's pieces where they are close
// enough to that path's own choice. It never writes anything but the term.
void Decoder::beside(Track& t, const float* encoded, uint64_t index) {
  auto& m = t.match;
  if (!m.active || m.next > index) return;
  const size_t from = m.next == index ? m.symbol : 0;
  m.next = index + 1; m.symbol = 0;
  if (!m.alive) return;
  std::array<float, blank_token + 1> s;
  for (size_t symbol = from; symbol < 10; ++symbol) {
    if (cancelled_.load()) throw std::runtime_error("decoder cancelled");
    if (!m.predicted) {
      m.prediction = backend_.predict(m.last, m.state);
      m.predicted = true;
    }
    if (cancelled_.load()) throw std::runtime_error("decoder cancelled");
    if (!backend_.scores(encoded, m.prediction, s)) { m.alive = false; return; }
    int64_t own = 0;
    for (int64_t id = 0; id <= blank_token; ++id) {
      if (!std::isfinite(s[size_t(id)])) throw std::runtime_error("nonfinite decoder score");
      if (s[size_t(id)] > s[size_t(own)]) own = id;
    }
    if (m.spelled) {
      // Spelled out; it is the term only if the word does not go on.
      if (own != blank_token && terms_->continues(own)) m.alive = false;
      return;
    }
    int64_t best = -1;
    for (const auto& held : m.at) for (const auto& step : terms_->steps(held.first, held.second))
      if (best < 0 || s[size_t(step.first)] > s[size_t(best)]) best = step.first;
    const float deficit = best < 0 ? 0 : s[size_t(own)] - s[size_t(best)];
    if (best < 0 || deficit > terms_->margin() || m.spent + deficit > terms_->budget()) {
      if (own != blank_token) m.alive = false; // this path would write something else here
      return;
    }
    std::vector<std::pair<uint16_t, uint16_t>> advanced;
    for (const auto& held : m.at) for (const auto& step : terms_->steps(held.first, held.second))
      if (step.first == best) advanced.push_back({held.first, step.second});
    m.at = std::move(advanced); m.spent += deficit;
    m.pieces.push_back({best, index});
    m.last = best; m.state = m.prediction.next; m.predicted = false;
    for (const auto& held : m.at) if (terms_->complete(held.first, held.second)) {
      m.spelled = true; m.term_words = terms_->words(held.first); m.at.clear();
      break;
    }
  }
}
// The decoder's own path has finished a word since the term began. Returns
// whether the term is settled: released in place of the decoder's words, or
// given up with those words released as they were. A term of more words
// than the decoder has written so far stays open, unless a mark ends it.
bool Decoder::word_ends(Track& t, bool mark, std::vector<Token>& out) {
  auto& m = t.match;
  if (m.alive && m.spelled && m.term_words == m.words) {
    out.insert(out.end(), m.pieces.begin(), m.pieces.end());
    m = {};
    return true;
  }
  uint16_t longest = m.spelled ? m.term_words : 0;
  if (!m.spelled) for (const auto& held : m.at) longest = std::max(longest, terms_->words(held.first));
  if (m.alive && !mark && m.words < longest) return false;
  out.insert(out.end(), m.held.begin(), m.held.end());
  m = {};
  return true;
}
// Nothing more will be written on this track: the term stands if it was
// spelled out for at least the words the decoder wrote; otherwise its own.
void Decoder::settle(Track& t, std::vector<Token>& out) {
  auto& m = t.match;
  if (!m.active) return;
  const bool stands = m.alive && m.spelled && m.term_words >= m.words;
  const auto& released = stands ? m.pieces : m.held;
  out.insert(out.end(), released.begin(), released.end());
  m = {};
}
// One frame. The decoder's own path is the plain greedy loop, unchanged by
// any term; only where its output goes, and whether a second path runs
// beside it, depends on the list.
void Decoder::frame(Track& t, const float* encoded, uint64_t index, std::vector<Token>& out) {
  auto& m = t.match;
  std::array<float, blank_token + 1> s;
  // Same ten-symbol-per-frame bound as the pinned reference. An exhausted
  // bound advances the frame; it does not fabricate blank or drop a token.
  for (size_t symbol = 0; symbol < 10; ++symbol) {
    if (cancelled_.load()) throw std::runtime_error("decoder cancelled");
    if (!t.predicted) {
      t.prediction = backend_.predict(t.last, t.state);
      t.predicted = true;
    }
    if (cancelled_.load()) throw std::runtime_error("decoder cancelled");
    int64_t next;
    const bool scored = terms_ && backend_.scores(encoded, t.prediction, s);
    if (!scored) next = backend_.classify(encoded, t.prediction);
    else {
      next = 0;
      for (int64_t id = 0; id <= blank_token; ++id) {
        if (!std::isfinite(s[size_t(id)])) throw std::runtime_error("nonfinite decoder score");
        if (s[size_t(id)] > s[size_t(next)]) next = id;
      }
    }
    if (cancelled_.load()) throw std::runtime_error("decoder cancelled");
    if (next < 0 || next > blank_token) throw std::runtime_error("decoder token range");
    if (next == blank_token) break;
    if (m.active && !terms_->continues(next)) {
      // The decoder's word ends here: let the second path see this frame, then settle.
      beside(t, encoded, index);
      if (!word_ends(t, !terms_->begins(next), out)) ++m.words;
    }
    if (!m.active && scored && terms_->begins(next)) {
      int64_t best = -1;
      for (const auto& start : terms_->starts())
        if (best < 0 || s[size_t(start.token)] > s[size_t(best)]) best = start.token;
      const float deficit = best < 0 ? 0 : s[size_t(next)] - s[size_t(best)];
      if (best >= 0 && deficit <= terms_->margin()) {
        m = {};
        m.active = m.alive = true; m.words = 1; m.spent = deficit;
        for (const auto& start : terms_->starts()) if (start.token == best) m.at.push_back({start.term, start.next});
        m.pieces.push_back({best, index});
        m.last = best; m.state = t.prediction.next; m.predicted = false;
        m.next = index; m.symbol = symbol + 1;
        for (const auto& held : m.at) if (terms_->complete(held.first, held.second)) {
          m.spelled = true; m.term_words = terms_->words(held.first); m.at.clear();
          break;
        }
      }
    }
    (m.active ? m.held : out).push_back({next, index});
    t.last = next;
    t.state = t.prediction.next;
    t.predicted = false;
  }
  beside(t, encoded, index);
}
std::vector<Token> Decoder::finish(uint64_t epoch, uint32_t track) {
  if (!epoch || epoch != epoch_ || track >= state_count) throw std::invalid_argument("stale decoder epoch");
  if (faulted_ || cancelled_.load()) throw std::runtime_error("decoder epoch retired");
  std::vector<Token> out;
  try {
    settle(tracks_[track], out);
    return out;
  } catch (...) {
    faulted_ = true;
    throw;
  }
}

std::vector<Token> Decoder::push(uint64_t epoch, uint32_t track, uint64_t first,
                                 const float* encoded, size_t frames, bool final) {
  if (!epoch || epoch != epoch_) throw std::invalid_argument("stale decoder epoch");
  if (track >= state_count || !encoded || !frames || frames > 128 ||
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
    for (size_t f = 0; f < frames; ++f) {
      // A word still unfinished after this many frames is settled as it stands.
      if (t.match.active && ++t.match.frames > match_frames) settle(t, out);
      frame(t, encoded + f * encoder_width, first + f, out);
    }
    if (final) settle(t, out);
    t.seen = true;
    t.end = first + frames;
    return out;
  } catch (...) {
    faulted_ = true;
    throw;
  }
}
}
