# Source-bound native voice carrier

The normal carrier now uses SDK `65c4427527d3701668038088c279104dba83a7e5`
and its generic `Control.Answer` admission contract. That SDK reserves queue
space for every admitted control's reply, so an engine-event backlog cannot
displace an answer and fault the session (`tests/test_plugin_sdk_pin.py`).
When its worker fails, the carrier hands the worker's final events on and
waits, within the table's `lane_flush_ms` (two seconds by default), for
`Session.Flush` to confirm they were written before it exits; otherwise it
reports their delivery unproven, and where that time is what passed it says
so with the number. That includes events the worker writes after a late reply: once the
private lane has failed, the carrier reads the worker's output to its end and
answers nothing more. One ordered private writer and reply reader retain the
control until the actual worker verdict arrives, but only within the carrier's
private admission bound, which the runtime profile's `limits` state
(`plugin/native/limits.go`): `control_ms`, two seconds by default, or what
the table gives storage and `control_ms` together (77 seconds by default) for
the `speaker.*` operations and `recording.record`, which read and publish
private storage. The operations the carrier serves itself from the private
files, without the worker (`recording.list`, `recording.delete`,
`recording.prune` and the `vocabulary.*` operations), are each given two
reads and two durable writes of the host's (`host_read_ms` and
`host_write_ms`, 34 seconds together by default), and say so when that time
is what ended them.
Past it the carrier ends the private lane and leaves the control unanswered,
which the host reports as admission unknown, never as a refusal. A
`speech.session.playback_report` that the worker holds until an in-flight
audio write's Ack is counted is given that write's time as well,
`audio_write_ms` and `control_ms` together (five seconds by default), so that
the worker's own deadline for a stalled audio pipe comes first. Neither
inference nor a delayed verdict holds the SDK admission lane. There is
no public dependency on the withdrawn PendingAdmission/AdmissionResult types.

A start is bounded by the same table. The worker has `ready_ms`, 175 seconds
by default, to report ready, counted from the carrier's own start: the check
of the runtime's files and the worker's spawn are inside it. As that passes
the carrier writes on its standard error that the worker did not report
ready, with the number of milliseconds, what it is counted from and the
member's name, and then ends its worker and itself.
The default is chosen, not measured: it ends five seconds inside the 180
seconds that each set of the package declares to the host as the allowance
for its start (`startup_ms`), so that the carrier has said what it waited for
before that allowance has passed. The carrier is not told what its set
declares, so the package's assembly holds the two together:
`scripts/assemble_guided_beta_candidate.py` refuses a set whose `ready_ms`
and two seconds for the carrier's word together pass that set's `startup_ms`.
The warm inference inside that start is given `warm_probe_ms`, 40 seconds by
default: the worker's library fails a start whose warm inference took longer,
and the carrier refuses a report of one, by the one member.

The engine's own waits are members of the table too, and the worker states
each to the code that waits by it: `model_call_ms` (30 seconds by default)
for one model inference, `input_tail_ms` (3 seconds) for a conversation's
last frames, `output_take_ms` (15 seconds) for synthesized audio waiting to
be taken, and `speaker_match_ms` (15 seconds) for a final's speaker. So are
the waits of the libraries under it: `endpoint_decision_ms` (one second) for
the endpoint model's verdict where a pause would end a turn, after which the
turn ends by silence alone and the worker's log says so;
`endpoint_retire_ms` (30.5 seconds: a model call's time and the margin) for a
question that model has not answered when the input ends; and
`separation_min_ms` and `separation_max_ms` (4 and 25 seconds), the bounds of
the budget one separation of competing talkers has, which must end inside a
model call. The session's header, the endpoint's gate and the separating
recognizer keep numbers for a caller that states none, a probe or a test; a
released engine does not run by them
(`runtime/native/session/c_api.h`, `aii_voice_open_bounded`).

A session that is ending is called stalled when nothing has moved for
`drain_idle_ms`, and that clock does not run while work with a limit of its
own is in flight: an operation on the host's storage, a model call or a write
of audio. That work's own limit is then the one that speaks
(`runtime/native/session/drain_hold.h`).

Retirement is bounded too, by the table. At teardown the carrier closes the
worker's input and waits `worker_exit_ms` for its process to exit: five and a
half seconds by default, which is the worker's own `retire_ms` of five and
the margin, so the worker's own deadline always comes first and says what had
not ended. Past that the carrier terminates the worker's process tree (on
Windows its ownership job, exit status 73), reports forced cleanup with the
member and the number, and waits `worker_reap_ms`, five seconds by default,
to see it gone. The worker answers that EOF by closing any session,
joining its I/O threads and closing its output. On Windows it then ends its
process at once with `TerminateProcess` (`end_process` in
`runtime/native/session/worker_io.h`) instead of freeing its models and
returning from `main`: on the Windows qualification VM that path took 3 to 4
seconds of the bound (the model release, then DLL process-detach work, most of
it torch_cpu deregistering its operators) and crossed it under host load. The
kernel reclaims the process's memory, handles and GPU allocations either way.
Linux and macOS workers still release their models and return from `main`.
This directory builds a thin carrier, not an installable plugin by itself. The
current native engine profile requires its sealed C++ runtime, verified models,
backend configuration and host activation bindings. The engine is native: a
packaged carrier starts only the native worker its bound profile names. A
profile that describes an interpreter, one that states a `python`, `bootstrap`
or `site` member as the retired Python engine's packages did, is refused by the
carrier at its start (`pythonProfileRefused` in `plugin/native/runtime.go`),
and no script in this tree builds, stages, binds or assembles one
(`tests/test_python_engine_is_not_built.py`). The Python in this tree is the
tests, the tooling under `scripts/`, and the Python engine kept as a double of
the native worker for the tests (`runtime/plugin_engine` and the Python
packages it imports). Neither model loading nor signing is performed by this
builder.

The landed pin preserves emitted confirmation flags and supports several
interfaces. Current emission contains eight `speech.session` controls,
ten `speaker.uid` operations, five `recording` operations and three
`vocabulary` operations. Eight speaker mutations, `recording.delete`,
`recording.prune`, `vocabulary.correct` and `vocabulary.forget` require
operator confirmation;
`recording.record` saves the current buffer without that extra confirmation.
The package builder partitions the actual emitted
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

On that date frozen Python CP1 profiles and files were left untouched and still
took their own explicit `-I -S -B` launch path beside the native one. Changing
this source does not relabel any frozen carrier as rebuilt or deployed. The
native launcher passed an actual compiled child check with empty PATH and no
Python entrypoint, plus ambiguity/integrity refusals; restoring the interpreter
launch compiled and failed that check. That proved the launcher, not a complete
Python-free speech engine, which had still to pass real STT/TTS/VAD/UID,
interruption, recovery and receipt/retirement gates before promotion. Model
selection and SDK authority were not changed by this seam.

That was the state on 2026-09-11. Since 0.1.0-beta.7 the Python-free engine is
what every released desktop set carries: the C++ worker and this Go carrier,
with no Python in the package. The interpreter's launch path is no longer in
the carrier either: a profile that states a `python`, `bootstrap` or `site`
member is refused at the carrier's start and nothing is started
(`TestAPythonProfilePackageIsRefusedAtStart` in
`plugin/native/runtime_python_profile_test.go`). The packer that built such a
runtime and the packaged interpreter's bootstrap are removed from this tree.

## Reproduce without modifying the shared repositories

From this repository's root, obtain `git archive` of the exact SDK revision from
the repository named in `sdk-source.json`. Save the unmodified tar at its
`archive` path and extract to its `source` path. The builder checks the tar's
SHA-256, every extracted file, absence of extra files, and the native Go module's
replacement binding. Do not substitute an arbitrary checkout of main.
The pinned authoring commit is not a public-mirror commit. The SDK owner is
handling public SDK source delivery separately; see `docs/DEVELOPMENT.md`.
The SDK pin, sealed archive bytes, native module replacement and qualified
carrier remain unchanged. No SDK source checkout is needed for installation.

With Go 1.27.0 installed on the Apple Silicon build machine:

```sh
python -m scripts.build_plugin_carrier
python -m scripts.build_plugin_carrier --verify
python -m scripts.build_plugin_carrier --bundle /absolute/new/carrier-source.zip
```

Outputs are under `.build/native-sdk-65c4427/`: macOS arm64 plain and race,
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

The carrier declares eight session controls, ten speaker-management operations,
five recording operations and three vocabulary operations
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
The current-host browser gate is
separate; historical results on another binding cannot certify a replacement
executable. Neither set of checks replaces installed physical browser
conversation or human-quality qualification.

Current assembly refuses a minimum host below AII OS 0.1.14, the first host
that reads the extent every runtime declares (`RUNTIME_EXTENT_MIN_HOST` in
`scripts/assemble_guided_beta_candidate.py`). The older floors, 0.1.8 for
engine-initiated input completion and 0.1.12 for component selection, lie
inside it. Output-only publication must additionally bind the release carrying
that host contract. Its accelerator declarations come from explicit reviewed
per-set inputs, never historical paths or blanket startup overrides.
Device memory is never omitted: every set declares `device_memory_bytes`, zero
for a CPU or Apple unified-memory set and a positive measured reservation for a
discrete GPU set (see `docs/RUNTIME_COMPONENT_SELECTION.md`). Hearing/speaking
scopes originate in the built worker, are serialized by this SDK, and are not
invented by packaging.
These declarations do not substitute for installed-path execution evidence.
