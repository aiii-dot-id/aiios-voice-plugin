#include "waveform_capture.h"
#include <cstring>
#include <iostream>

static void check(bool ok, const char* why) {
  if (!ok) { std::cerr << why << '\n'; std::exit(1); }
}
static uint32_t le32(const std::string& s, size_t at) {
  return uint32_t(uint8_t(s[at])) | uint32_t(uint8_t(s[at+1])) << 8 |
         uint32_t(uint8_t(s[at+2])) << 16 | uint32_t(uint8_t(s[at+3])) << 24;
}
int main() {
  using aii::voice::RecentWaveforms;
  RecentWaveforms history;
  history.begin("first");
  std::vector<float> pcm(48000, .25f);
  history.feed(0, pcm.data(), pcm.size());
  check(history.available(), "buffered microphone signal unavailable before STT final");
  history.end(); // The tool turn may follow microphone/session closure.
  check(history.available(), "buffered audio lost at session end");
  auto recording = history.take_latest();
  check(recording.session == "first" && recording.start == 0 &&
        recording.end == 48000 && recording.pcm.size() == 48000,
        "last buffered recording differed");
  const auto wav = RecentWaveforms::wav(recording.pcm);
  check(wav.compare(0, 4, "RIFF") == 0 && wav.compare(8, 4, "WAVE") == 0 &&
        le32(wav, 24) == 16000 && le32(wav, 40) == 96000,
        "retrospective WAV extent differs");
  RecentWaveforms::wipe(recording.pcm);
  check(history.available() == 0, "single-use recording replayed");
  history.begin("second");
  history.feed(0, pcm.data(), pcm.size());
  auto current = history.take_latest();
  check(current.session == "second" && current.pcm.size() == 48000,
        "current session buffer was not selected");
  RecentWaveforms::wipe(current.pcm);
  check(!history.available(), "current buffer replayed");
  history.feed(48000, pcm.data(), pcm.size());
  history.end();
  history.begin("third");
  check(history.available(), "new session with no audio discarded last buffer");
  auto prior = history.take_latest();
  check(prior.session == "second" && prior.start == 48000 && prior.end == 96000,
        "last session buffer changed across empty successor");
  RecentWaveforms::wipe(prior.pcm);
  history.begin("retry");
  history.feed(0, pcm.data(), pcm.size());
  auto unsaved = history.latest();
  RecentWaveforms::wipe(unsaved.pcm); // Simulate a failed private publication.
  check(history.available() && history.latest().end == 48000,
        "failed private save consumed the only buffered audio");
  auto saved = history.latest();
  history.feed(48000, pcm.data(), pcm.size());
  history.consume_saved(saved.session, saved.end);
  check(history.available() && history.latest().start == 48000 &&
        history.latest().end == 96000,
        "verified save consumed newer unsaved microphone audio");
  RecentWaveforms::wipe(saved.pcm);
  history.end();
  auto closed = history.latest();
  history.consume_saved(closed.session, closed.end);
  check(!history.available(), "verified closed-session save remained replayable");
  RecentWaveforms::wipe(closed.pcm);
  history.clear();
  check(history.available() == 0, "clear retained private audio");
  return 0;
}
