#include "drain_hold.h"
#include <iostream>

using aii::voice::DrainHold;
using aii::voice::SnapshotBridge;
using namespace std::chrono_literals;
using Clock = DrainHold::Clock;
using By = DrainHold::By;

static int failures = 0;
static void check(bool ok, const char* what) { if (!ok) { std::cerr << "FAIL: " << what << '\n'; ++failures; } }

int main() {
  const auto t0 = Clock::now();
  const auto idle = 15000ms;
  const DrainHold::ModelCalls quiet{false, 4};
  {
    DrainHold hold;
    auto deadline = t0 + idle;
    check(hold.look({false, 7}, quiet, false, false, t0 - 1s, idle, deadline) == By::nothing && deadline == t0 + idle, "work that ended outside a drain moved a deadline");
    check(hold.look({false, 7}, quiet, false, true, t0 + 16s, idle, deadline) == By::nothing && deadline == t0 + idle, "a drain with nothing in flight was held, or what ended before it was its progress");
    check(hold.look({true, 7}, quiet, false, true, t0 + 16s, idle, deadline) == By::storage && deadline == t0 + idle, "storage in flight did not hold the drain, or renewed its deadline");
    check(hold.look({true, 7}, quiet, false, true, t0 + 60s, idle, deadline) == By::storage && deadline == t0 + idle, "storage still in flight stopped holding the drain");
    check(hold.look({false, 8}, quiet, false, true, t0 + 61s, idle, deadline) == By::nothing && deadline == t0 + 61s + idle, "the drain's idle limit was not counted from when its storage ended");
    check(hold.look({false, 8}, quiet, false, true, t0 + 62s, idle, deadline) == By::nothing && deadline == t0 + 61s + idle, "an operation that ended was counted twice");
    check(hold.look({true, 9}, quiet, false, true, t0 + 63s, idle, deadline) == By::storage && deadline == t0 + 63s + idle, "an ended operation with another in flight was not counted");
  }
  {
    DrainHold hold;
    auto deadline = t0 + idle;
    hold.look({false, 0}, quiet, false, false, t0, idle, deadline);
    check(hold.look({true, 0}, quiet, false, true, t0 + 1s, idle, deadline) == By::storage && deadline == t0 + idle, "storage in flight inside the deadline moved it");
    check(hold.look({false, 1}, quiet, false, true, t0 + 4s, idle, deadline) == By::nothing && deadline == t0 + 4s + idle, "storage that ended inside the deadline did not move it when it ended");
    check(hold.look({false, 1}, quiet, false, true, t0 + 4s + idle + 1ms, idle, deadline) == By::nothing && deadline == t0 + 4s + idle, "the drain was given its idle limit a second time for the same storage");
  }
  {
    DrainHold hold;
    auto deadline = t0 + idle;
    hold.look({false, 7}, quiet, false, false, t0, idle, deadline);
    check(hold.look({false, 7}, {true, 4}, false, true, t0 + 1s, idle, deadline) == By::model_call && deadline == t0 + idle, "a model call in flight renewed the deadline");
    check(hold.look({false, 7}, {false, 5}, false, true, t0 + 3s, idle, deadline) == By::nothing && deadline == t0 + 3s + idle, "the drain's idle limit was not counted from when its model call ended");
    check(hold.look({false, 7}, {true, 5}, false, true, t0 + 29s, idle, deadline) == By::model_call && deadline == t0 + 3s + idle, "a model call in flight past the deadline did not hold the drain");
    check(hold.look({false, 7}, {false, 6}, false, true, t0 + 30s, idle, deadline) == By::nothing && deadline == t0 + 30s + idle, "a model call that ended past the deadline did not move the drain from then");
    check(hold.look({false, 7}, {false, 6}, false, true, t0 + 46s, idle, deadline) == By::nothing && deadline == t0 + 30s + idle, "a model call that ended was counted twice");
  }
  {
    DrainHold hold;
    auto deadline = t0 + idle;
    hold.look({false, 0}, quiet, false, false, t0, idle, deadline);
    check(hold.look({false, 0}, quiet, true, true, t0 + 16s, idle, deadline) == By::audio_write && deadline == t0 + idle, "a write of audio in flight did not hold the drain, or renewed its deadline");
    check(hold.look({false, 0}, quiet, false, true, t0 + 17s, idle, deadline) == By::nothing && deadline == t0 + idle, "a write of audio that is over went on holding the drain, or moved its deadline here");
    check(hold.look({true, 0}, {true, 4}, true, true, t0 + 18s, idle, deadline) == By::storage, "storage in flight was not named before a model call and a write");
    check(hold.look({false, 0}, {true, 4}, true, true, t0 + 18s, idle, deadline) == By::model_call, "a model call in flight was not named before a write");
  }
  {
    DrainHold hold;
    auto deadline = t0 + 45s;
    hold.look({false, 0}, quiet, false, false, t0, idle, deadline);
    check(hold.look({false, 1}, quiet, false, true, t0 + 1s, idle, deadline) == By::nothing && deadline == t0 + 45s, "work that ended shortened a deadline that was later");
  }
  {
    DrainHold hold;
    auto deadline = t0 + 5s;
    check(hold.look({true, 0}, {true, 4}, true, false, t0 + 6s, idle, deadline) == By::nothing && deadline == t0 + 5s, "work in flight held an abort");
    check(hold.look({false, 3}, {false, 9}, false, false, t0 + 6s, idle, deadline) == By::nothing && deadline == t0 + 5s, "work that ended renewed an abort's deadline");
    check(hold.look({false, 3}, {false, 9}, false, true, t0 + 7s, idle, deadline) == By::nothing && deadline == t0 + 5s, "what ended before a drain was counted as its progress");
  }
  if (!failures) std::cout << "a drain is held by storage, a model call or a write of audio in flight, and its idle limit runs from the look that saw its last work end PASS\n";
  return failures ? 1 : 0;
}
