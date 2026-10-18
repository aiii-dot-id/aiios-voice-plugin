# Native ECAPA frontend

Feature extraction for the SpeechBrain ECAPA-TDNN encoder. The UID library
links it statically when built with `AII_UID_ECAPA=ON`. It owns no audio device,
model download, speaker registry, or session.

## Numerical contract

The C ABI in `frontend.h` accepts mono little-endian PCM16 at 16 kHz, up to
30 seconds per call. It returns 80 feature columns and `1 + samples / 160`
frames. Silence is mathematically valid; the caller must admit usable speech.

The SpeechBrain 1.1.1 reference uses a 400-sample periodic Hamming window,
160-sample stride, centered constant padding, squared magnitude, 80 triangular
mel filters, 10-log power with a 1e-10 floor, an 80 dB sequence-relative floor,
and per-band sentence mean subtraction without variance normalization.
Do not substitute a Kaldi frontend or reflection padding. The sparse table
skips only exact zero coefficients and preserves accumulation order. Tables
have internal linkage; C++ implementation symbols are hidden from other
inference libraries in the process.

Failures and cancellation leave the output untouched. Work and memory are
bounded by the input limit. Cancellation is checked during transform and
normalization loops, before publishing output. Independent calls can run
concurrently; there is no mutable speaker state.

## Build and test

Supply `POCKETFFT_ROOT` containing `pocketfft_hdronly.h` from upstream revision
`0fa0ef591e38c2758e3184c6c23e497b9f732ffa`. No build-time network access is used.
PocketFFT is BSD-3-Clause; retain its upstream LICENSE.md with distributed
binaries. See https://github.com/mreineck/pocketfft/tree/0fa0ef591e38c2758e3184c6c23e497b9f732ffa.

```sh
cmake -S runtime/native_uid_ecapa -B build/ecapa \
  -DPOCKETFFT_ROOT=/path/to/pinned/pocketfft -DCMAKE_BUILD_TYPE=Release
cmake --build build/ecapa
ctest --test-dir build/ecapa --output-on-failure
```

Contract tests include an independent synthetic SpeechBrain feature oracle,
input limits, silence, cancellation without partial output, and concurrency.
Regenerate coefficients and the synthetic fixture only with SpeechBrain 1.1.1:

```sh
python generate_tables.py /tmp/tables.h --reference /tmp/reference_test.h
```

The reference is SpeechBrain's Apache-2.0 feature implementation:
https://github.com/speechbrain/speechbrain/tree/v1.1.1/speechbrain/processing.
Generated fixtures contain no recordings, transcripts, embeddings or user data.
