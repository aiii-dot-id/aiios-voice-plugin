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
// Internal model composition, not a new SDK protocol. Anonymous acoustic tracks
// are not enrolled people. No enrollment labels or reference words enter here.
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
  // Every piece's score for the same step, blank last; false when this
  // backend can only classify. classify must be the highest of these.
  virtual bool scores(const float* frame, const Prediction&, std::array<float, blank_token + 1>&) { (void)frame; return false; }
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
  void clone_track(uint64_t epoch,uint32_t source,uint32_t target);
  // `final` says no frame follows on this track in this epoch: a term still
  // being followed is settled and nothing stays withheld.
  std::vector<Token> push(uint64_t epoch, uint32_t track, uint64_t first,
                          const float* encoded, size_t frames, bool final = false);
  // For a track that will receive no further frame in this epoch: what a
  // term still being followed withheld, settled as push(final) settles it.
  std::vector<Token> finish(uint64_t epoch, uint32_t track);
  void cancel() noexcept;
  // Terms to lean toward, from the next reset on. Owner only. Null or an
  // empty list is plain greedy decoding, token for token what it always was.
  void boost(std::shared_ptr<const TermBoost> terms) { pending_ = std::move(terms); }
  // The longest a term is followed before it is given up, in encoder frames.
  static constexpr size_t match_frames = 32;
 private:
  // A TERM BEING WEIGHED. The decoder writes what it always wrote: its own
  // path never changes, so its state and everything it writes outside a term
  // are exactly the plain decoder's. Where it begins a word and a listed term's
  // first piece scores within the margin of its choice, a second path is run
  // beside it over the same frames, taking the term's pieces while each scores
  // within the margin of that path's own choice and their sum within the
  // budget. The decoder's own word is withheld meanwhile. When its word ends
  // (it begins another word, writes a mark, or the utterance ends): if the
  // second path spelled the term out and did not go on with the word, the
  // term's pieces are released in place of the decoder's word; otherwise the
  // decoder's word is released as it was. A term takes the place of the
  // word, or words, it would be; nothing else is touched.
  struct Match {
    bool active = false, alive = false, spelled = false;
    // The second path.
    State state;
    Prediction prediction;
    int64_t last = blank_token;
    bool predicted = false;
    uint64_t next = 0;  // the next frame it has not seen
    size_t symbol = 0;  // where it resumes in the frame the term began
    float spent = 0;
    uint16_t term_words = 0;
    std::vector<std::pair<uint16_t, uint16_t>> at;
    std::vector<Token> pieces;
    // The decoder's own words since the term began, and their number.
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
