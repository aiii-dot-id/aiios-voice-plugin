# Desktop beta.3 candidate — 2026-09-17

This is a new immutable candidate, not a relabel of beta.2 and not a public
release. The operator requested the agreed declarations plus installed STT,
TTS, UID, VAD, interruption and recovery validation. The authoritative evidence
root is `/path/to/work/voice-beta3-20260917-r1/` on the build Mac.
Its final `RESULTS.json` and `REPORT.md` separate engine, installed, physical
audio, distribution and publication evidence.

## Exact inputs and output

- Voice source base: `7d05e2ff2a9fddf0cb59ce1bfa59cc1e061aa968`, with the
  candidate changes on `candidate/desktop-beta3-20260917`.
- SDK: `d75105d53db01b8399e9fb67a37850cc0ffe36d9`, archive SHA-256
  `e37a5e48ae186128b4eb3ad7cfae41643136d85008c0bcab6909a50a182b2ed2`.
- Installed host baseline: `d5aa2a8bbfa4ef5b13ce715ee54fe2b4b24e6557`,
  AII OS 0.1.7. Later facility commits are not silently included in these tests.
- Signed package: `signed/id.aiii.voice-0.1.0-beta.3.aiiospkg`, SHA-256
  `6bee4d769e0b1d44e7f4cf8431fe6195ecdc179057835b583ca215bf9819a822`.

There is one package with three native variants. Runtime/model acquisition
selects only the current platform. Each fresh installed identity must acquire
exactly 24 selected model/data files and one matching runtime archive; a foreign
variant request fails the probe. The 25-model union in the package is not a
requirement to download 25 models on each machine.

## Agreed declarations

Minimum host is 0.1.7. Every accelerator profile declares `startup_ms=180000`,
subject to the host's operator ceilings and overrides. No identity configuration
edit is required or performed. The installed probe requires a readback whose
allowance source is `package`, with no override or cap.

| Variant | Declared host-memory reservation | Device memory |
| --- | ---: | --- |
| macOS arm64 | 8,589,934,592 bytes | Omitted: unknown |
| Linux x86_64 | 8,486,555,648 bytes | Omitted: unknown |
| Windows x86_64 | 7,482,712,064 bytes | Omitted: unknown |

Reservations are carried forward unchanged, not claimed as new measured peaks.
Unknown device memory is not serialized as zero. Session limit remains one;
there is no new accelerator fallback or driver installation.

Seven operator settings keep their existing values and ranges:

| Scope | Settings |
| --- | --- |
| Hearing | Recognition language, VAD pause (320–5000 ms, default 768), VAD threshold (0.05–0.95, default 0.5) |
| Speaking | TTS voice (10 named voices, default Alba), speaking language, variation, seed |

VAD is always enabled. These settings take effect at the next session; their
scope is not a claim of live mid-utterance reconfiguration. This compact profile
supports **English only** for recognition and synthesis. Other languages are
not advertised as available. TTS voice selection is unrelated to speaker UID.

## Fresh execution and trust evidence

All three rebuilt native runtime/carrier combinations pass the four recorded
SDK groups: current-generation interruption/recovery, durable guided enrollment,
speaker enrollment/management, and 13 settings cases including all ten voices.
Windows runs in the ordinary interactive VM desktop session, not service
session zero. Final Authenticode-signed Windows bytes were re-tested. macOS
libraries use the existing local signing arrangement; do not infer Developer ID
notarization from the T3 signature.

The final T3 package verifies against the platform trust root with an independently
built, clean, VCS-stamped host at the stated revision. An appended-byte tamper is
refused. The distribution host binary originally considered for this verifier
lacked a VCS stamp; it was not allowed to pass as a source-bound verifier.

Source gates: 239 required tests, no failure/error/skip; 26 focused packaging and
binding tests; Go carrier whole package plain and race; fresh native ASR/session
contracts. Counts overlap across runs and must not be summed as unique coverage.

## Installed gate and its limits

The host production source is unmodified. An external test overlay creates a
fresh isolated identity, installs the exact signed package, acquires and verifies
assets from an explicit local fixture origin, and launches the contained native
engine. It then exercises:

1. AI-visible schemas for all six speaker management operations.
2. Explicit recorded enrollment capture; close the session; list the durable
   handle; propose enrollment; confirm exact arguments through the host's act
   mechanism; read the host-written state; observe the known fixture speaker.
3. The shipped browser voice module and real 48 kHz Web Audio clock: two exact
   recorded transcripts, partials, TTS, local stop, VAD-triggered spoken
   interruption, retained opening words, full recovery replies and receipts.
4. Finish/drain with exact input/output sample accounting, followed by a fresh
   session and disconnect-to-Abort, endpoint release and process retirement.

macOS installed r5 passes this complete sequence. Windows installed r5 passes
the functional sequence, including UID and all browser tail/retirement checks,
but fails the cancellation-completion latency target (568.8 ms versus <250 ms;
local playback stops in 2.5 ms). Its overall test exit remains nonzero. The
Windows and Ubuntu final statuses are recorded in the evidence root, including
every failed attempt.
Ubuntu's ordinary-user installed path currently refuses before model execution:
AppArmor denies bubblewrap's network-namespace setup on linux-test-host Ubuntu 24.04. Root
execution is not substituted for this user gate, and no security policy is
disabled. The host agent owns the supported installer/preflight resolution.

The first Windows browser attempt measured 1.8 ms local playback stop and
625 ms until the cancellation event, exceeding the probe's 250 ms target.
The test was corrected to retain this failure while continuing downstream
functional checks. There is no held-ack injection in this installed test; the
old diagnostic naming a held acknowledgement was incorrect. A later pass does
not erase the retained first latency miss.

UID evidence proves the capture/enrollment/observation mechanism on public
recordings, not reliable human authentication or population-wide false-accept
rates. The installed known-speaker probe reuses its enrollment recording; the
separate SDK probes carry additional recordings. This task does not qualify
allow/ignore speaker filtering through an installed conversational UI.

## Handoff and publication boundary

`publication-inputs/inventory.json` binds the signed package, three runtime
archives and eight release-owned data assets (12 files total). Seventeen
additional model/data assets have pinned upstream URLs and expected hashes.
No GitHub upload or catalog mutation occurred. The release-owned beta.3 URLs
are not yet public, so public fresh-download qualification remains open.

The notice index retains two explicit provenance limitations; collection of
license texts is not clearance or a source-reproduction proof. No existing
identity, enrollment or earlier package is replaced by this task. No physical
microphone/speaker, live Test Identity A/Test Identity B conversation, mobile, multilingual or
human-level listening qualification is claimed.
