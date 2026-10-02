# Voice failure and recovery

An unresolved speaker is not a failed speech engine. The diagnostic
`anonymous_profile_needs_corroboration` preserves an older profile without
claiming its UUID, changing its evidence or relaxing match thresholds. It must
cross the registry-to-attribution boundary as an uncertain observation; the
next utterance remains usable in the same session.

The worker writes its first fatal reason as a bounded, JSON-escaped
`AII_VOICE_FAILURE` diagnostic before cleanup. The subsequent carrier EOF is
not the cause. Diagnostics contain no waveform, transcript or profile document.

A process restart and model readiness are not recovery of a speech session.
The host must retire the old transport and bind the new process generation and
audio endpoints. The failed conversation stays closed. Recovery makes a fresh
operator-requested session possible; it never silently reopens the microphone.

Tests must exercise the actual observation mapper after producing a legacy
profile diagnostic, and recovery tests must use the production recovery path,
not manually reconnect a driver to make an otherwise disconnected process pass.
