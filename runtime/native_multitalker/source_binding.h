#pragma once
#include "../native/session/session.h"
#include <array>
#include <atomic>

namespace aii::multitalker {
using Waveforms = std::array<std::vector<float>,2>;
// A waveform or recognized source result outside this composition's contract.
// It is an integrity fault, distinct from a model call that failed to run.
struct SourceContractViolation:std::invalid_argument {using std::invalid_argument::invalid_argument;};
// Finite capture composition, not a new speaker identity or streaming slot.
// The model owner serializes calls and keeps both models alive. cancel() on
// the recognizer and the caller's cancellation flag are the concurrent lane.
// No result escapes until both independently recognized sources validate.
std::vector<aii::voice::RecognizedSegment> bind_source_text(
    aii::voice::Recognizer&,const Waveforms&,uint64_t capture,
    const std::atomic<bool>& cancelled,const std::function<void()>& completed_stage={});
// Same numerical rule as scripts.separator_audio; raw scale-invariant model
// outputs must never be saturated into PCM and then treated as good evidence.
Waveforms normalize_sources(const std::vector<float>& mixture,Waveforms raw);
}
