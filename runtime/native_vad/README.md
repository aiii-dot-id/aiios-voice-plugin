# Native control VAD

This is the private, Python-free C++ control component for the native voice
engine, not a Plugin SDK extension or a complete plugin. It runs the **same**
2,243,022-byte Silero checkpoint as `voice_core/control_vad.py`; no model,
threshold, language, resampler or speech policy has been substituted.

The existing immutable asset owner verifies SHA-256
`a4a068cd6cf1ea8355b84327595838ca748ec29a25bc91fc82e6c299ccdc5808`
before handing bytes to this ABI. The component retains them for its lifetime,
checks graph names/arity and output geometry, and has no network loader.

Contract:

- One control-thread owner; independent of STT and TTS inference queues.
- Exactly 512 finite mono float32 samples at 16 kHz. No implicit resampling.
- Preserve 64 context samples and recurrent state `[2,1,128]` across blocks.
- Invalid input is refused without changing state, output or the sample clock.
- An inference/output failure faults the owner; it must be recreated.
- Reset clears state, context and the sample clock together.
- Set `ORT_DISABLE_TELEMETRY=1` before initialization. Creation refuses an
  absent/contradictory setting. The release recipe should compile telemetry
  out as well; API-only suppression failed normal shutdown in the ASR proof.
- CPU execution is explicit, one intra/inter-op thread. This small recurrent
  control task is not evidence of a GPU inference path for STT or TTS.

Build with explicit `ORT_INCLUDE` and `ORT_LIBRARY` using CMake. The CLI
`aii_vad_probe model.onnx input.f32` exists for evaluation only. The linked
library/CLI require no Python, Torch, package manager, PATH discovery or
development files. Windows/mobile compilation and session integration are
separate uncompleted gates.

Evidence: `deliverables/native-vad-cpp-20260911-r1` and its regression sibling.
Python is used only as the independent evaluation harness/reference, not by
the child executable.
