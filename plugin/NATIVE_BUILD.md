# Source-bound native voice carrier

The normal carrier now uses SDK `8155af048f312faec9000defe294b0086b28b62c`
and its generic `Control.Answer` admission contract. One ordered private writer
and reply reader retain the control until the actual worker verdict arrives;
neither inference nor a delayed verdict holds the SDK admission lane. There is
no public dependency on the withdrawn PendingAdmission/AdmissionResult types.
This directory builds a thin carrier, not an installable plugin by itself. The
current native engine profile requires its sealed C++ runtime, verified models,
backend configuration and host activation bindings. Historical Python profiles
remain separate. Neither model loading nor signing is performed by this builder.

The landed pin preserves emitted confirmation flags and supports several
interfaces. Enrollment-enabled emission contains eight `speech.session`
controls plus six `speaker.uid` operations; only the latter's five mutations
require operator confirmation. The package builder partitions the actual emitted
methods rather than hiding enrollment beneath the lifecycle interface.

## Native engine transition (2026-09-11, operator approved)

The voice-owned carrier source now also admits a sealed native worker profile:
`backend: "native"` plus `native: "engine/<executable>"` in its private
`voice-runtime.json`. This is **not** a new SDK/package-manifest field. The
entrypoint must be an executable member of the complete hash-bound runtime;
Python/bootstrap/site fields must be empty. It cannot name the carrier or its
manifest, escape the root, search PATH or fall back to an interpreter. No shell
or developer worker arguments are used. The existing inherited audio handles,
control transport, model directory and process ownership remain unchanged.

Frozen Python CP1 profiles and files are untouched and still take their existing
explicit `-I -S -B` launch path. Changing this source does not relabel any frozen
carrier as rebuilt or deployed. The native launcher passes an actual compiled
child check with empty PATH and no Python entrypoint, plus ambiguity/integrity
refusals; restoring the interpreter launch compiles and fails that check.
This proves the launcher, not a complete Python-free speech engine. The latter
must pass real STT/TTS/VAD/UID, interruption, recovery and receipt/retirement gates
before promotion. Model selection and SDK authority are not changed by this seam.

## Reproduce without modifying the shared repositories

From this repository's root, obtain `git archive` of the exact SDK revision from
the repository named in `sdk-source.json`. Save the unmodified tar at its
`archive` path and extract to its `source` path. The builder checks the tar's
SHA-256, every extracted file, absence of extra files, and the native Go module's
replacement binding. Do not substitute an arbitrary checkout of main.
The pinned authoring commit is not a public-mirror commit. The exact sealed
SDK source tar is included in beta.4's prepared developer assets; see
`docs/DEVELOPMENT.md` for its size, hash and planned release URL. The SDK pin,
archive bytes, native module replacement and qualified carrier remain unchanged.

With Go 1.27.0 installed on the Apple Silicon build machine:

```sh
python -m scripts.build_plugin_carrier
python -m scripts.build_plugin_carrier --verify
python -m scripts.build_plugin_carrier --bundle /absolute/new/carrier-source.zip
```

Outputs are under `.build/native-sdk-8155af0/`: macOS arm64 plain and race,
Ubuntu/Linux amd64 and Windows amd64 plain executables plus `build.json`.
The build inventory records exact source digests, toolchain, target, race mode
and executable hashes. Source changes during compilation fail the build. An
existing directory is refused, not overwritten; use a new explicit `--output`
for a separate comparison. Standard proof defaults select the pinned directory
and verify its current source/binary inventory before starting a worker. Old
`.build/aii-voice-t3*` and isolated candidates remain historical evidence and
are never fallback choices. Explicit candidate arguments remain available.

The carrier/source ZIP is review and reproduction material, **not** an
`.aiiospkg`, model bundle or installer. It contains no credentials, enrollment,
user recordings or model weights. The manifest is an integrity inventory, not
a publisher signature or new authority. No network fetch, signing, shared
repository write, installation or audio-device access happens during build.

## Launch boundary

The carrier declares the SDK's eight controls and six speaker-management operations
without loading a worker when `AIISDK_DESCRIBE=1`. Installed native activation
is zero-argument and binds its worker from the sealed runtime manifest; explicit
worker commands are a development-proof path, not an installed requirement.
Browser audio remains
host-owned; the carrier forwards the inherited audio endpoints and never opens
a microphone or speaker. The engine does not embed the host's conversation LLM.

The installed wrapper must supply its complete runtime/dependency/model closure
through the SDK's existing package/activation mechanisms. It must not assume a
developer environment, advertise a cross-compile as target qualification, or
invent new manifest fields. Mobile needs its actual SDK loading and native
backend implementations, not these desktop subprocess binaries.

The new binding passes complete SDK suites plain and race on Mac, repeated
native-carrier race tests, held-ack interruption, and two actual-model Mac
speech cycles with opening words, receipt-driven drain, Abort and reuse.
Windows/Ubuntu execution evidence for the current beta.4 carriers is recorded
in `docs/DESKTOP_BETA4_HANDOFF_20260918.md`. The current-host browser gate is
separate; historical results on another binding cannot certify a replacement
executable. Neither set of checks replaces installed physical browser
conversation or human-quality qualification.

Current assembly excludes AII OS before 0.1.8 for engine-initiated input
completion. Output-only publication must additionally bind the release carrying
that host contract. Its accelerator declarations come from explicit reviewed
per-platform inputs, never historical paths or blanket startup overrides.
Device memory is omitted where unknown. Hearing/speaking scopes originate in
the built worker, are serialized by this SDK, and are not invented by packaging.
These declarations do not substitute for installed-path execution evidence.
