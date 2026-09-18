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
