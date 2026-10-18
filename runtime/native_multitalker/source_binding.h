#pragma once
#include "../native/session/session.h"
#include <array>
#include <atomic>

namespace aii::multitalker {
using Waveforms = std::array<std::vector<float>,2>;
struct SourceContractViolation:std::invalid_argument {using std::invalid_argument::invalid_argument;};
std::vector<aii::voice::RecognizedSegment> bind_source_text(
    aii::voice::Recognizer&,const Waveforms&,uint64_t capture,
    const std::atomic<bool>& cancelled,const std::function<void()>& completed_stage={});
Waveforms normalize_sources(const std::vector<float>& mixture,Waveforms raw);
}
