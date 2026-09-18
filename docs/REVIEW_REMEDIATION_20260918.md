# Voice review remediation

Base: `fd6ebb6610a3b73b6dc8612fd69ec82b4feb3002`. Source and deterministic
contract proofs are not installed-product, acoustic-quality or release proof.

## V1 — Graceful drain progress

The 15-second bound is now an inactivity bound during graceful drain, renewed
only by measured recognition, synthesis retirement/generated samples, audio
delivery, or accepted advancing rendering evidence. Admission is already closed;
status calls, duplicate receipts and foreign sessions cannot extend it. Abort
still has its independent five-second bound. Missing terminal receipts fault,
never become successful playback.

Validation: 23 Python worker/control/capture tests and 29 native CTest cases
passed. The new long-playback test fails unchanged on the original worker at
the old 15-second wall deadline and passes on the correction. Duplicate and
foreign-report traffic still terminate a stalled drain. Tests use the production
state machine and explicitly fake models, not physical speakers.

## V2 — Explicit, recoverable UID reset

`speaker.list` now distinguishes typed absence, incompatible documents and corrupt
documents. Storage read errors still refuse: an unreadable store is not empty.
No speakers/eligible captures are fabricated when recovery is needed. The result's
`recovery` object gives the two exact observed hashes (or `absent`). With speech
closed, an AI may propose `speaker.reset` with those two fields nested in
`recovery`; the existing host operator-confirmation gate remains mandatory.

Recovery first writes both original byte sequences into a content-addressed
`uid/recovery-<sha256>.json` archive through the existing host private-file broker,
CAS, durability receipt and readback. Only after that proof does it clear pending
captures and publish an empty profile bound to the current model. Archives cannot
be overwritten with different content; a separately confirmed retry re-attests
the same bytes. A partial reset reports its archive and unresolved state and
requires new inspection/confirmation. Recovery does not convert embeddings or
change identification into authorization. Archives remain private biometric
material; there is no automatic cleanup or public telemetry of their contents.

Native tests cover old-model/corrupt/zero-byte/missing profiles, old captures,
fresh enrollment after recovery, stale confirmation, denied reads, failed and
unsynced archive publication, and each partially failed reset. Carrier tests
hold the exact argument, confirmation, fixed-path and immutable-archive bounds.

## V3 — Reject the ambiguous host version

The candidate now declares a minimum of 0.1.8. All 0.1.7 builds are excluded,
including the ones that accepted the settings but could not stop browser capture
when the engine completed input. This is a minimum requirement, not a claim
that a new host release has already shipped. The host agent has been asked to
assign and qualify the supporting release. Publication/installed-product closure
still requires that release and the finite-limit/status-reconciliation journey.
