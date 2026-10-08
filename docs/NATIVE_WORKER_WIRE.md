# The carrier and worker wire

The native speech engine is two processes. The carrier is a Go program
(`plugin/native/`). It speaks the plugin kit's protocol to the host. The
worker is a C++ program (`runtime/native/session/worker.cpp`). It holds the
models. The carrier starts the worker and talks to it on the worker's
standard input and output in JSON lines. The host's audio reaches the worker
on two more pipes in a binary frame format.

This document specifies that private wire as it is in this tree today. Every
rule names the file and function it was read from. Something that was not
found in the code is marked "not determined". Nothing here is a promise to a
host or to a plugin author: the public contract is the plugin kit's, and
[NATIVE_SESSION_CONTRACT.md](NATIVE_SESSION_CONTRACT.md) states what the
engine does for a session.

Three programs speak this wire today:

- the carrier: `plugin/native/main.go`, `settings.go`, `snapshot.go`,
  `vocabulary.go`, `limits.go`, `runtime.go`, `handles_unix.go`,
  `handles_windows.go`;
- the worker: `runtime/native/session/worker.cpp`, `worker_io.cpp`,
  `worker_json.h`, `snapshot_bridge.cpp`, `worker_limits.h`,
  `worker_liveness.h`;
- the stand-in the tests use in the carrier's place: class `Worker` in
  `scripts/prove_native_worker_transport.py`.

Section 7 lists where they differ. A test,
`tests/test_native_worker_wire_document.py`, reads the carrier's and the
worker's source and fails when a name on the wire is missing from this
document, or when the first table of section 7 no longer says what the code
does.

Words used here: a "line" is one JSON object followed by one newline. A
"whole number" is a JSON number that is an integer from 0 to 2^53 - 1
(`worker_json.h`, `integer`). A "digest" is 64 lowercase hexadecimal
characters, a SHA-256.

## 1. Process start

### 1.1 The command

A packaged carrier has a runtime binding compiled in. It verifies the runtime
beside its own executable against that binding and starts the file the
runtime profile `voice-runtime.json` names in its member `native`, by its
full path, with no argument and with the runtime's directory as the working
directory (`runtime.go`, `verifyRuntime`, `packagedRuntime`). A profile that
names an interpreter (`python`, `bootstrap` or `site`) is refused and nothing
is started (`runtime.go`, `verifyRuntime`).

A carrier with no binding is a development carrier. It starts the command
given on its own command line, first word and arguments as given, and
inherits the working directory (`runtime.go`, `workerCommand`). A packaged
carrier refuses such arguments.

A worker started with no argument reads `native-profile.json` from its
working directory and takes every model path from under the directory
`AII_MODELS_DIR` names (`worker.cpp`, `main`; `installed_profile.h`,
`InstalledProfile::read`). A worker started with arguments takes seven model
paths, then optionally a backend, then optionally a speaker model and a
policy file, then optionally an earlier policy file: 7, 8, 10 or 11 arguments
(`worker.cpp`, `main`). A worker started with the single argument
`--describe-settings` writes its settings declaration as one line on standard
output and exits 0 without opening the wire.

### 1.2 The environment

The packaged carrier passes its own environment on, with these changes
(`runtime.go`, `packagedRuntime`, `packagedWorker`):

| Variable | What the carrier does |
|---|---|
| names beginning `PYTHON`, `DYLD_` or `LD_` | removed |
| `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `ORT_DISABLE_TELEMETRY` | removed, then set to `1` |
| `AII_VOICE_COREML_CACHE_DIR` | removed; set to the profile's bound cache directory when the profile has one |
| `LD_LIBRARY_PATH` | on Linux, set to the profile's bound library directories when it lists any |
| `AII_VOICE_CARRIER_LIVENESS_FD` | removed; set again as below |
| `AII_VOICE_LIMITS` | removed; set to the carrier's own table (1.3) |
| `AII_MODELS_DIR` | required and passed on; the carrier does not start without it |

The development carrier passes its environment on and replaces only
`AII_VOICE_LIMITS`, with the default table (`runtime.go`, `workerCommand`).

Both then add the audio and liveness descriptors (`handles_unix.go` and
`handles_windows.go`, `inheritAudio`):

- `AII_AUDIO_IN_FD` and `AII_AUDIO_OUT_FD`. The carrier is itself given these
  two by the host. On Linux and macOS it duplicates them, hands the copies to
  the worker as descriptors 3 and 4, and sets the two variables to `3` and
  `4`. On Windows it duplicates the two handles as inheritable and sets the
  variables to the new handle values in decimal. The worker reads input
  frames from the first and writes output frames to the second
  (`worker.cpp`, `Worker` constructor; `worker_io.cpp`, `audio_descriptor`).
  The worker refuses a value that is absent, empty, begins with a minus sign
  or is not a decimal number.
- `AII_VOICE_CARRIER_LIVENESS_FD`, on Linux and macOS only, always `5`. It is
  the read end of a pipe whose write end only the carrier holds. Nothing is
  ever written to it. The worker reads it from before it loads a model
  (`worker_liveness.h`, `watch_carrier_liveness`). The worker refuses to start
  when the value is not a decimal number from 3 to 1024. On Windows there is
  no such pipe: the carrier puts itself and its children in one job that ends
  with it (`job_windows.go`, `ownWorkerTree`).

On Linux and macOS the worker is started in a process group of its own
(`handles_unix.go`, `inheritAudio`). Its standard error is the carrier's
standard error (`main.go`, `run`), which is the host's log.

The worker also reads two opt-in variables from the environment it is given:
`AII_VOICE_STARTUP_TRACE` and `AII_VOICE_TTS_PHASE_TRACE`, each acted on only
when it is `1` (`runtime/native/platform/startup_trace.h`;
`tts_phase_trace.h`). The carrier neither sets nor removes them. The worker
sets some variables for the libraries inside its own process before it loads
a model (`worker_environment.h`); they are not part of this wire.

### 1.3 The limits table

`AII_VOICE_LIMITS` holds every time limit the worker waits by, for what it
asks of its carrier, for its own work, and for the engine it runs. The
carrier computes the table and the worker does no arithmetic on it. The table
has twenty-four members, each a whole number of milliseconds:

| Member | What the worker waits this long for |
|---|---|
| `exchange_read_ms` | the carrier's `snapshot_reply` to one page read |
| `exchange_write_ms` | the carrier's `snapshot_reply` to one stage or one publish |
| `opening_ms` | the carrier's `settings_reply` while a session is opening |
| `whole_read_ms` | one whole read of a private file: every page and the page that reads it back |
| `whole_publication_ms` | one whole publication: its stages, the publish and the read that verifies it |
| `drain_idle_ms` | a draining session with nothing moving and no storage, model call or write of audio in flight, before it is failed |
| `reply_settings_ms` | the settings a reply asked for as it began, before the reply is spoken in the voice already in force |
| `abort_ms` | an aborted session's core retiring, counted from the first abort |
| `capture_close_ms` | an enrollment capture's close, its preparation included |
| `session_open_ms` | a session's open returning after its settings arrived |
| `opening_notice_ms` | an opening before it says what it is waiting for (section 6) |
| `audio_write_ms` | one write of audio to the host's pipe, before the pipe is called stalled (5.3) |
| `control_write_ms` | one write of a line to the carrier, before the control channel is called stalled (section 3) |
| `retire_ms` | its own end, counted from when it begins to end, before it gives up with status `72` (1.5) |
| `capture_tail_ms` | the frames of an enrollment capture up to the sample it was told it ends at, counted from when it was told, before the capture is failed |
| `model_call_ms` | one model inference, by the session's watchdog, before the session is failed |
| `input_tail_ms` | the frames of a conversation up to the sample it was told it ends at, before the session is failed |
| `output_take_ms` | synthesized audio waiting for room in the session's bounded queue, which the worker empties by writing it to the host, before the session is failed |
| `speaker_match_ms` | a final's speaker, before the wait is declared over and the final's speaker is uncertain |
| `warm_probe_ms` | the warm inference of its start (1.4); one that took longer fails the start |
| `endpoint_decision_ms` | the endpoint model's verdict where a pause would end a turn, counted from when it was asked, before the turn is left to end by silence alone |
| `endpoint_retire_ms` | each question the endpoint model has not answered when a conversation's input ends, before the session is failed |
| `separation_min_ms` | the least one separation of competing talkers is given before it is given up |
| `separation_max_ms` | the most one such separation is given; between the two it has five times the separated audio's length |

The last nine are the engine's own. The worker hands seven of them to the
session as it opens each one (the first three and the last four), and
`warm_probe_ms` to the model owner as it warms (`worker.cpp`, `settings`,
`main`; `c_api.h`, `aii_voice_open_bounded`, `aii_voice_models_warm_within`),
and keeps `speaker_match_ms` itself (`attribution.h`). The session passes the
endpoint's two to its pause gate and the separation's two to its recognizer
(`runtime/native_endpoint/pause_gate.h`, `configure_waits`;
`runtime/native_multitalker/separating_recognizer.h`, `bound_separation`).
The session's header, the gate's and the recognizer's keep numbers of their
own for a caller that states none, a probe or a test; a worker never runs by
them. When one of the session's passes and fails it, the `reason` of the
`failure` gives the wait's own words, its number and the member: `synthesis
model call exceeded progress deadline: 30000 ms, the time the limits table
gives it (model_call_ms)`, and the same form after `input tail missing at
admitted cutoff` for `input_tail_ms`, after `audio consumer did not release
bounded output` for `output_take_ms`, and after `semantic endpoint did not
retire` for `endpoint_retire_ms` (`session.cpp`, `passed`). Two of them fail
nothing. A verdict that is late leaves its turn to end by silence alone, and
the worker's log says so with the member and its number (section 6). A
separation that is given up at its budget leaves its turn the words that
were heard unseparated, and the recognizer's own report counts it
(`budget_expired`).

The form is strict (`worker_limits.h`, `WorkerLimits::parse`): one JSON
object with exactly these members, each once, in any order, no white space,
each value one to nine digits with no sign and no leading zero. Anything else
is refused. A variable that is absent or empty gives the worker its compiled
defaults. A table that is refused stops the worker before it loads a model,
with exit status 1.

The worker's ranges (`worker_limits.h`, `WorkerLimits::validate`):
`reply_settings_ms` and `endpoint_decision_ms`, the two waits a listener
feels, are 10 to 2000; every other member is 250 to 600000. It
also requires that `exchange_write_ms` is at least `exchange_read_ms`, that
`whole_read_ms` is at least `exchange_read_ms`, and that
`whole_publication_ms` is at least `exchange_write_ms` plus `whole_read_ms`.

The carrier does not state these members directly. It states twenty-seven
numbers, in the profile's member `limits` or by default, and computes the
table from them (`limits.go`, `limitsFrom`, `forWorker`): `host_read_ms`,
`host_write_ms`, `storage_wait_ms`; nineteen that pass through unchanged,
`drain_idle_ms`, `reply_settings_ms`, `abort_ms`, `capture_close_ms`,
`session_open_ms`, `opening_notice_ms`, `audio_write_ms`,
`control_write_ms`, `retire_ms`, `capture_tail_ms`, `model_call_ms`,
`input_tail_ms`, `output_take_ms`, `speaker_match_ms`, `warm_probe_ms`,
`endpoint_decision_ms`, `endpoint_retire_ms`, `separation_min_ms` and
`separation_max_ms`; and five it does not hand over, because only the carrier waits by them: `control_ms`, how long it
waits for the worker's answer to a request (section 4); `ready_ms`, how long
the worker has to report `ready`, counted from the carrier's own start (1.4);
and the three waits of the carrier's own end, `worker_exit_ms`,
`worker_reap_ms` and `lane_flush_ms` (1.5). The first three are how long the
host has
for one read, for one durable write, and for a session's settings or one
whole read of a file. Each computed member is the host's time for the same
thing plus a fixed margin, so the carrier always answers before the worker
stops waiting (`limits.go`, `workerExchange`, `workerOpening`, `wholeRead`,
`wholePublication`). The carrier's own ranges and nesting rules are in
`limits.go`, `valid`. The numbers and the arithmetic live in two places and
are not restated here: `plugin/native/limits.go` and
`runtime/native/session/worker_limits.h`.

Where the carrier waits for something the worker bounds itself, the
carrier's wait is the outer one by that margin, and never an equal number.
`worker_exit_ms`, the carrier's wait for the worker's exit, must be
`retire_ms` and the margin at least, or the table is refused: a worker that
is ending inside its own time is not killed. The engine's waits nest the
same way, each by the margin and each refused where it does not:
`output_take_ms` must be `audio_write_ms` and the margin at least, because
the worker cannot take more audio from the session while a write of it is in
flight, and a pipe the host has stopped taking is then said by the pipe's own
deadline; `drain_idle_ms` must be `input_tail_ms` and the margin at least, so
that last frames that do not come are said as that and not as a stalled
drain; `ready_ms` must be `warm_probe_ms` and the margin at least;
`endpoint_retire_ms` must be `model_call_ms` and the margin at least, because
a question the endpoint has not answered is a model call in flight and the
watchdog's limit is the one to speak; and `model_call_ms` must be
`separation_max_ms` and the margin at least, because a separation runs
inside one model call and is given up at its budget, which fails nothing,
where the watchdog would fail the session. `separation_max_ms` is not less
than `separation_min_ms`. `endpoint_decision_ms` is the outer of nothing: the
question still running is not ended by it. A
model call is not inside `session_open_ms`: an open loads a model and the
watchdog does not watch it. `speaker_match_ms` is the outer of nothing: the
match still running is not ended by it, and one that comes later is not used.
The operations on the private
files that the carrier serves itself, without the worker (the `recording`
list, deletion and pruning, and the three `vocabulary` operations), are each
given two reads and two durable writes of the host's, computed from
`host_read_ms` and `host_write_ms` (`limits.go`, `ownStorage`); it was ten
seconds typed at each.

One rule about the table is not the carrier's, because the carrier is not
told the number it needs. A set declares to the host an allowance for its
start, `startup_ms` in the package's accelerator declaration. The set's
`ready_ms` and two seconds for the carrier's word must end inside it, so
that the carrier has said what it waited for (1.4) before that allowance has
passed. The package's assembly is the one place where a set's profile and
its declaration are both in hand, and it refuses a set that breaks the rule,
naming both numbers (`scripts/assemble_guided_beta_candidate.py`,
`readiness_inside_startup`; `scripts/runtime_limits.py`,
`startup_covers_readiness`).

### 1.4 Readiness

The worker writes nothing on the control channel until its models are loaded
and one warm inference has run. Then its first line is `ready`
(`worker.cpp`, `Worker::run`):

```json
{"ready":{"identity":{"backend":"native-common-cpu"},"readiness":{"models_loaded":5,"accelerator":"cpu","accelerator_scope":"...","probe_ms":1234}}}
```

| Member | Meaning |
|---|---|
| `identity` | an object with one member, `backend`: `native-common-cpu`, `native-common-vulkan` or `native-common-metal` (`worker.cpp`, `backend_name`). A build may compile in another name for the first; the tests' fixture worker is built as `fixture-native`. |
| `readiness` | an object with the four members below |
| `models_loaded` | 4, or 5 when the speaker model is loaded (`native_c_api.cpp`, `warm`) |
| `accelerator` | `cpu`, `cpu_vulkan`, `cpu_metal`, or `external_recognizer` when a recognizer was supplied from outside (`native_c_api.cpp`, `warm`) |
| `accelerator_scope` | fixed text saying what `accelerator` describes |
| `probe_ms` | how long the warm inference took, at least 1; the worker fails its start past `warm_probe_ms`, 40000 by default (`c_api.cpp`, `aii_voice_models_warm_within`) |

The carrier waits for this line until `ready_ms` have passed since its own
start, 175 seconds by default (`main.go`, `awaitReady`). The wait is counted
from the instant the carrier's process initialised, the same instant its
startup records count `elapsed_ms` from (`startup.go`, `startupBegan`): the
check of its runtime's files and the worker's spawn are inside it. Time that
is already spent when the worker has been started leaves nothing to wait.
When no `ready` came in that time the carrier writes on its
standard error, as the wait passes, that the worker did not report ready
within that many milliseconds of the carrier's start, with the member's name
and how long into that time the worker was started, and a startup record
with the phase `worker-readiness-not-reported`. It then ends as it ends for
any reason (1.5), with status 1 and the same sentence as its reason, and
never opens the public lane. A worker that is still loading its models does
not read its input (`worker_liveness.h`), so ending it takes the carrier's
`worker_exit_ms` and a kill: the sentence is written before that, not after
it. The default is chosen, not measured: it ends five seconds inside the 180
seconds that each set of the package declares to the host for its start
(1.3).

The carrier accepts a
`ready` with a `readiness` member only when `models_loaded` is 4 or 5,
`probe_ms` is 1 to the table's `warm_probe_ms`, and the pair of `accelerator`
and `backend` is one it knows, which are the three `native-common` backends,
each with its accelerator; a `probe_ms` past that member is refused with
both numbers and the member's name (`main.go`, `readinessReport`). It then writes the line `AII_VOICE_READY` on
its own standard error (section 6) and opens the public lane. A `ready`
without a `readiness` member is accepted only when `backend` is
`deterministic-test-not-real-model`; the carrier then opens the lane and
writes no `AII_VOICE_READY` line. A second `ready` is a fault (`main.go`,
`read`).

### 1.5 What ends the worker

The worker's main loop ends for one of three reasons (`worker.cpp`,
`Worker::run`):

1. Its standard input reached its end. Controls already read are answered
   first. A session that is still open is aborted.
2. One of its four I/O threads reported a fault: a malformed or truncated
   audio frame, a control line over its bound, a write that failed or passed
   its deadline.
3. An exception reached the loop: a control line that is not JSON, a request
   whose `id` is not greater than the last, a `snapshot_reply` that no
   request of this worker asked for, a failure of the core.

From that moment the worker has `retire_ms`, five seconds by default, for
all of its end: the session and any storage operation retiring, and then its
threads ending (`worker.cpp`, `begin_retiring`). When that passes it writes
an `AII_VOICE_FAILURE` line that names the member, its number and what had
not ended, and ends with status `72` (`retire_passed`). An aborted session
that has not retired in `abort_ms` ends the worker the same way and says
that member instead (`end_unretired`).

| Exit status | Meaning |
|---|---|
| 0 | the worker ended with no engine failure and no transport fault. A failure that belonged to one session, which that session reported, does not count (`worker.cpp`, `fail_session`, `Worker::run`). Also the exit of `--describe-settings`. |
| 1 | an engine failure stood at the end, or a transport fault occurred, or the worker could not start: its limits, its liveness descriptor, its arguments or its models were refused. A start failure writes `native voice worker: ` and the reason on standard error (`worker.cpp`, `main`). |
| `72` | the worker's own deadline passed and it ended itself at once, having said which: an aborted session whose core did not retire in `abort_ms`, or a session or a thread that did not retire in `retire_ms` at the end (`worker.cpp`, `abandon`). |
| `74` | Linux and macOS: the liveness pipe reached its end, so the carrier is gone (`worker_liveness.h`). |
| `75` | Linux and macOS: the liveness pipe yielded a byte or a read error. |
| `73` | Windows: the carrier ended the whole job, itself included, because the worker did not exit (`handles_windows.go`, `killWorker`). |

An engine failure is recorded when it happens and cleared when the next
`speech.session.open` is accepted (`worker.cpp`, `open`), so status 1 reports
the failure that stood when the worker ended.

A failure the core raises on one of its own threads reaches the worker in the
core's status, which the worker reads twice in each pass of its loop. It
looks for the failure at both readings. The second is the one it ends a
session on, so a core that fails and retires between the two is not released
with its failure unsaid (`worker.cpp`, `pump`, `fail_engine`).

When the carrier ends, it closes the worker's input and waits
`worker_exit_ms` for the worker to exit: five and a half seconds by default,
the worker's `retire_ms` and the margin, so that the worker's own deadline
comes first. Then it kills the worker's process group (Linux and macOS) or
ends its job (Windows) and waits `worker_reap_ms`, five seconds by default,
to see the worker gone (`main.go`, `endWorker`; `handles_unix.go` and
`handles_windows.go`, `killWorker`). Where a private fault ended the lane
while the public lane was still open, it then gives the host's lane
`lane_flush_ms`, two seconds by default, to take the worker's last events and
confirm them written (`main.go`, `handoff`). Each of the three, when it is
what passed, is said on the carrier's standard error with the member's name
and its number. The model release that follows a worker's own end on Linux
and macOS is not inside `retire_ms`; a set whose release is slow states a
longer `worker_exit_ms`.

The carrier holds no writing end of the worker's audio input: it hands on the
two ends its host gave it and closes its own copies only after it has waited
for the worker's end (`handles_unix.go` and `handles_windows.go`,
`inheritAudio`; `main.go`, `run`, `endWorker`). That pipe therefore ends when
the host closes its end and not before. A worker whose audio input ends
while a session is live fails that session as an engine failure (5.2), so
whoever holds the writing end ends the control channel first and keeps the
audio input open until the worker has exited. The stand-in closes in that
order (`prove_native_worker_transport.py`, `close`).

## 2. The control channel, carrier to worker

The carrier writes lines to the worker's standard input. There are three
kinds: a request, a `settings_reply` and a `snapshot_reply`. One writer writes
them in the order they were queued (`main.go`, `startLane`).

The worker refuses a line longer than 1048576 bytes, a last line with no
newline, a NUL byte, the escape for NUL, an object with two members of one
name, nesting deeper than 32, and more than 10000 values (`worker_io.cpp`,
`Pipe::line`; `worker_json.h`, `parse`, `validate`). Each of these ends the
worker (1.5). The worker holds at most 64 lines it has read and not yet
handled, and stops reading while it holds them (`worker.cpp`,
`start_threads`).

### 2.1 The request line

```json
{"id":7,"operation":"speech.session.status","arguments":{"session_id":"s-1"}}
```

| Member | Rule |
|---|---|
| `id` | a whole number, greater than the `id` of every earlier request. The carrier counts from 1 by 1 (`main.go`, `enqueue`). The worker ends on an `id` that is not greater than the last (`worker.cpp`, `Worker::run`). |
| `operation` | a string of 1 to 256 bytes |
| `arguments` | an object. The carrier passes on the bytes the host sent (`main.go`, `enqueue`). |

Every request gets exactly one reply line with the same `id` (3.1), unless
the worker ends first. The carrier holds at most 64 requests unanswered and
at most 64 queued to write (`main.go`, `enqueue`).

### 2.2 The session operations

The worker has one session at a time. Its `lifecycle` is `closed`, `opening`,
`open`, `draining` or `failed`. Every session operation but
`speech.session.open` requires `arguments.session_id` to be the current
session's, and is otherwise refused with `stale session` (`worker.cpp`,
`admit`). A refusal is a reply with `error`. Every accepted reply carries
`accepted`, true.

| Operation | Arguments | Result |
|---|---|---|
| `speech.session.open` | `session_id` (1 to 128 bytes, never used before in this worker); `output_handle`; `audio`, an object with `output` (`rate` 8000 to 192000, `channels` 1 or 2), `input` (the same two members, or null for a session with no input), optionally `format`, which must be `s16le`; `input_handle` when there is an input and absent when there is none. Inside `audio.input`, optionally `stream` (the number of the input stream, 5.2), `processing` (what the page reported about its capture) and `channel_roles`. Optionally `enrollment_capture`, an object with exactly `consented` (true), `request_id` (a digest) and `created_ms`. | `session_id`; `state` (the `lifecycle`); `purpose` (`conversation` or `enrollment_capture`); `audio`, with `input` (`rate` 16000, `channels` 1, or 2 with `channel_roles`, or null) and `output` (`rate` 24000, `channels` 1) |
| `speech.session.status` | `session_id` | the session's state: `model_execution`, `session_id`, `state_sequence`, `attributions`, `lifecycle`, `opening` (only while opening: `waiting_for`, `waited_ms`), `reason`, `operator_settings`, `corrections` (when a list was handed over), `purpose`, `enrollment_capture`, `input`, `recognition`, `input_completion`, `synthesis`, `playback`, `bookkeeping` (`worker.cpp`, `status`) |
| `speech.session.finish_input` | `session_id`; `stream_id`, which must be the open's `input_handle`; `end_sample` | `stream_id`, `end_sample`. Accepted while the session is still opening, and applied when it opens. |
| `speech.session.synthesize` | `session_id`; `synthesis_id` (never used before in this worker); `text` (1 to 32000 bytes) | `synthesis_id`; `output_stream`, the number of the audio stream the reply is written on |
| `speech.session.stop_playback` | `session_id`; `synthesis_id`, or absent, null or empty for the current reply | `synthesis_id`, `output_stream` (both null when there is no current reply), `output_fenced` (true), `playback_verified` (false) |
| `speech.session.cancel_synthesis` | as `speech.session.stop_playback` | as `speech.session.stop_playback` |
| `speech.session.playback_report` | exactly five members: `session_id`, `synthesis_id`, `output_stream`, `rendered_samples`, `terminal` (a boolean) | `synthesis_id`, `output_stream`, `rendered_samples`, `terminal` |
| `speech.session.close` | `session_id`; `mode`, `abort` or `drain` | `mode` |

`speech.session.open` is answered at once, before the session is open. The
worker first writes the event `session_start`, then a `settings_request`
(3.4), then the reply. The session is open when the event `session_ready`
arrives. An enrollment capture asks for no settings and is ready at once.

`speech.session.synthesize` writes a `settings_request` with `refresh` before
its reply (`worker.cpp`, `ask_speech`).

A `speech.session.playback_report` may be answered later than requests that
followed it: the worker holds a report that counts samples of an audio write
still in flight until that write has ended (`worker.cpp`, `unacknowledged`,
`reconcile`).

An operation name the worker does not know is refused like any other request
it cannot serve.

### 2.3 The speaker operations

Ten operations manage enrolled speakers: `speaker.list`, `speaker.enroll`,
`speaker.remove`, `speaker.reset`, `speaker.discard_capture`,
`speaker.upgrade_policy`, `speaker.buckets`, `speaker.associate`,
`speaker.forget` and `speaker.link`. The carrier checks their arguments and
passes them on (`enrollment.go`, `validateEnrollment`); the worker checks
them again and does the work (`worker.cpp`, `enroll`). They need no open
session, except `speaker.enroll` with `finals`; `speaker.upgrade_policy` and
a `speaker.reset` with `recovery` need speech closed.

| Operation | Arguments the worker reads |
|---|---|
| `speaker.list` | optionally `session_id` |
| `speaker.enroll` | `speaker_id`, `label`, and either `capture_id` (a digest) or `session_id` with `finals` (one to eight `sequence` numbers of this session's `transcript_final` events) |
| `speaker.remove` | `speaker_id` |
| `speaker.reset` | nothing, or `recovery`: an object with `enrollment_sha256`, `captures_sha256` and, when the list reported one, `speaker_registry_sha256` |
| `speaker.discard_capture` | `capture_id` |
| `speaker.upgrade_policy` | nothing |
| `speaker.buckets` | nothing |
| `speaker.associate` | `speaker_uuid`, `registry_revision`, `display_label`, optionally `external_id` |
| `speaker.forget` | `speaker_uuid`, `registry_revision` |
| `speaker.link` | `speaker_uuid`, `target_uuid`, `registry_revision` |

Every one but `speaker.list` and `speaker.buckets` must carry the host's
record of the operator's confirmation in `_host_operator_act`, an object with
`id` and `confirmed_at`. The worker takes each `id` once, except the kit's
standing `auto` (`confirmed_acts.h`).

The reply comes when the work has ended, which may be after later requests
were answered. Its `result` is an object with `status` (`succeeded` or
`failed`), `operation_result`, and, when the status is `failed`, `reason` and
`detail`. The worker adds `session_open` and `speech`, a short copy of the
session's status, to every `operation_result` (`worker.cpp`, `pump`). The
other members of `operation_result` are the plugin's public output and are
held by `plugin/native/schemas/speaker.output.json` and
`speaker-buckets.output.json`; they are not listed here. One speaker
operation runs at a time; a second is refused while the first is in flight.

### 2.4 The recording operations

`recording.record` and `recording.status` travel on this wire
(`worker.cpp`, `waveform_control`). The carrier accepts no argument of the
caller's for either (`waveform.go`, `validateWaveform`). `recording.status`
is answered at once. `recording.record` is answered when the file has been
published and read back, or refused when it could not be. Both results are an
object with `status` and `operation_result`; its members are held by
`plugin/native/schemas/recording.output.json`.

`recording.list`, `recording.delete` and `recording.prune` do not travel on
this wire. The carrier answers them itself from the host's private files
(`main.go`, `admit`; `recording_store.go`, `recordingStore`).

### 2.5 The vocabulary operations

`vocabulary.list`, `vocabulary.correct` and `vocabulary.forget` do not travel
on this wire. The carrier owns the correction list and answers them itself
(`main.go`, `admit`; `vocabulary.go`, `vocabularyStore`). The list reaches
the worker only inside a `settings_reply` (2.6).
[RECOGNIZER_CORRECTIONS.md](RECOGNIZER_CORRECTIONS.md) describes the list.

### 2.6 `settings_reply`

The carrier's answer to a `settings_request` (3.4). It has no top-level `id`.

```json
{"settings_reply":{"id":3,"session_id":"s-1","values":{"tts_voice":"alba"},"corrections":{"schema":"aiii.voice.corrections","revision":2,"rules":[]}}}
```

| Member | Rule |
|---|---|
| `id`, `session_id` | the request's, unchanged |
| `values` | present on success: the host's settings object, as the host gave it (`settings.go`, `settingsValues`). The worker reads eight keys from it and refuses an opening session on any other (`operator_settings.h`, `OperatorSettings::read`). |
| `corrections` | present when a correction list is stored and the store yielded it in time, and only in the answer to a request without `refresh`: the stored document's exact bytes (`vocabulary.go`, `sessionCorrections`). A list the worker cannot hold does not fail the session; the session runs uncorrected and says so (`worker.cpp`, `settings`; `corrections_wire.h`). |
| `error` | present on failure: fixed text. The host's own words are never passed on. |
| `reason_code` | present on failure, written by the carrier: `HOST_SETTINGS_NO_ANSWER` (the host gave no answer in its time), `HOST_SETTINGS_ERROR` (the host answered an error) or `HOST_SETTINGS_NOT_SETTINGS` (what it answered is not settings) (`settings.go`, `readSettingsBounded`). The worker reads it and says which of the three it was (`worker.cpp`, `settings_failure`); a code it does not know, or none, is said as `host settings unavailable` |

The carrier gives the host `storage_wait_ms` for an opening's settings and
correction list together, and `host_read_ms` for a refresh (`settings.go`,
`serveSettings`).

### 2.7 `snapshot_reply`

The carrier's answer to a `snapshot_request` (3.5). It has no top-level `id`.

| Member | Rule |
|---|---|
| `id`, `session_id` | the request's, unchanged |
| `value` | present on success: the host's result object for the file call, after the carrier has checked it (`snapshot.go`, `snapshotValue`) |
| `error` | present on failure: fixed text |
| `reason_code` | present on some failures: `FS_NOT_FOUND` (the first page of a read found no file), `FS_GENERATION_MISMATCH` or `FS_DIGEST_MISMATCH` (the host's two codes for a conflict, which the worker reads on a publish), or `HOST_STORAGE_NO_ANSWER` (the host gave no answer in its time; this one is the carrier's own code and not the host's) (`snapshot.go`, `snapshotValue`, `readSnapshot`) |

The members of `value` the worker reads (`snapshot_bridge.cpp`, `read_owned`,
`publish`):

| Request | Members of `value` |
|---|---|
| a page read | `offset`, `size`, `bytes`, `data_b64`, `eof`, and `sha256` (the digest of the whole file) on a page asked with `digest` |
| a stage | `bytes` (of this stage), `size` (staged so far) |
| a publish | `size`, `sha256`, `replaced`, `durable`, `durability` (`synced` or `file-synced` when durable, `unknown` when not) |

The worker hands a publication's receipt on inside its results with one
member added, `readback_verified`.

The carrier gives the host `host_read_ms` for a page read and `host_write_ms`
for a stage or a publish (`snapshot.go`, `readSnapshot`).

## 3. The control channel, worker to carrier

The worker writes lines on its standard output. There are five kinds: a
reply, an event, `ready` (1.4), a `settings_request` and a
`snapshot_request`. One writer thread writes them in the order they were
queued. The worker holds at most 128 lines and 1048576 bytes not yet written;
a line beyond that is not queued and its sender fails (`worker.cpp`, `send`).
A line that is not wholly written `control_write_ms` after its write began,
three seconds by default, is a transport fault: the worker says so on its
standard error and ends with status 1 (`worker_io.cpp`, `Pipe::expired`;
`worker.cpp`, `Worker` constructor, `Worker::run`). Two threads can see that
deadline, the one that writes and the main loop, and what is said is the
pipe's one sentence whichever sees it first: `native pipe write expired:
3000 ms, the time the limits table gives it (control_write_ms)`. On Windows
a write is one blocking call that only the main loop's cancellation ends, so
there the loop is the only one that sees it. A write that
is stopped inside its time, because the worker is ending or its other pipe
has failed, is a different event and says `native pipe write interrupted`,
which names no limit. The carrier's one reader
takes the worker's lines as they come and waits on nothing else (`main.go`,
`read`), so such a write stalls only when the carrier is not running.

The carrier classifies a line by the first of these members it carries:
`snapshot_request`, `settings_request`, `ready`, `event`, then a non-zero
`id`. A line with none of them is a fault (`main.go`, `read`). The carrier
reads lines with a buffer of 1048576 bytes; a longer line ends its reading.

### 3.1 The reply line

```json
{"id":7,"result":{"accepted":true}}
{"id":8,"error":"stale session"}
```

| Member | Rule |
|---|---|
| `id` | the request's `id` |
| `result` | present when the worker did what was asked: an object |
| `error` | present when it refused: text for the caller |

The carrier hands `result` or `error` to the host as the control's answer
(`main.go`, `read`). An `id` that no unanswered request has is a fault.

### 3.2 The event line

```json
{"event":{"type":"session_ready","session_id":"s-1","sequence":2,"id":"s-1:2","observed_monotonic_ns":123,"models":{"backend":"native-common-cpu"}}}
```

The carrier passes the object in `event` to the host unchanged and in order
(`main.go`, `forward`). Every event carries `type`, `session_id`, `sequence`
(counted from 1 in each session), `id` (the session's id, a colon and the
sequence) and `observed_monotonic_ns` (`worker.cpp`, `emit`). After a session
has failed the worker emits no event for it but `failure`. After an abort the
core's events are not published; the session still ends with `session_end`.

| `type` | Further members | When |
|---|---|---|
| `session_start` | none | an open was accepted |
| `session_ready` | `models`: `backend`, `operator_settings`, and `corrections` when a list was handed over; for a capture, `backend` and `purpose` | the session is open |
| `speech_start` | `start_sample`, `end_sample` | speech began |
| `transcript_partial` | `start_sample`, `end_sample`, `text`; with corrections applied also `recognized_text` and `corrections` (how many) | the recognizer's words so far changed |
| `transcript_final` | as `transcript_partial`; `track_id` when the words belong to one separated track; `attribution` (`decision`, `reason`, `speaker`, `speaker_id`, `revision`, `used_for_permissions`) | an utterance ended |
| `turn_committed` | `start_sample`, `end_sample`, and in `text` the reason the turn ended | a turn ended |
| `speaker_observation` | `refers_to` (the `sequence` of the final it is about), `decision` (`known`, `unknown` or `uncertain`), `reason`, `speaker`, `speaker_id`, `revision`, `late`, `used_for_permissions`, `track_id`, `start_sample`, `end_sample`; with evidence from one separated track also `evidence_scope`, and where the registry gave them `speaker_uuid`, `registry_revision`, `continuity`, `display_label`, `match`, `score` | who spoke a final was decided, or will not be (`attribution.h`; `speaker_observation.h`) |
| `input_finished` | `stream_id`, `end_sample`, `processed_end_sample`, and `reason` when the core gave one | the input ended and everything before its end was heard |
| `synthesis_start` | `synthesis_id`, `output_stream` | a reply began |
| `interruption_requested` | `synthesis_id`, `output_stream`, `reason` (`vad_speech`, `stop_playback` or `cancel_synthesis`) | a reply was fenced |
| `synthesis_end`, `synthesis_cancelled` | `synthesis_id`, `output_stream`, `delivered_samples`, `generated_samples`, `playback_verified` | a reply's last audio frame, its END, was written and the core retired it |
| `playback_observation` | `synthesis_id`, `output_stream`, `sample_rate`, `rendered_samples`, `delivered_samples`, `discarded_samples`, `terminal`, `outcome` (`progress`, `drained` or `stopped`), `evidence`, `playback_verified` | a `speech.session.playback_report` changed what is known |
| `session_end` | `status` (`completed` or `aborted`), `scope`, `playback_verified`, `input_samples`, `model_padding_samples`; for a capture also `enrollment_capture` | the session ended and its resources are released |
| `failure` | `reason`, `scope`, `resources_released`, `playback_verified`, `attributions` | the session ended in failure and its resources are released |

The core also numbers five kinds the worker never emits: `pause_query` and
`pause_resolved` are dropped (`worker.cpp`, `pump`); `pause_late`, the
endpoint's verdict not come in `endpoint_decision_ms`, is turned into a line
of the log (section 6) and dropped; and
`endpoint_scheduling` and `endpoint_work` are written only by a core built
for Android (`session.cpp`, `endpoint_loop`). Whether a worker is ever built
that way is not determined; if it were, it would emit them with
`start_sample`, `end_sample` and `text` like any other.

### 3.3 `ready`

Section 1.4.

### 3.4 `settings_request`

```json
{"settings_request":{"id":3,"session_id":"s-1"}}
{"settings_request":{"id":4,"session_id":"s-1","refresh":true}}
```

| Member | Rule |
|---|---|
| `id` | a whole number counted by the worker from 1, in a counter of its own: it is not a request `id` |
| `session_id` | the session asking, 1 to 128 bytes |
| `refresh` | absent, or true. Absent: a session is opening and waits for its settings and its correction list. True: an open session asks, as a reply begins, for the settings alone (`worker.cpp`, `open`, `ask_speech`). |

The worker writes no other top-level member on the line.

### 3.5 `snapshot_request`

The worker's request to read or replace one of the plugin's private files. It
names a file by a fixed word, never by a path.

| Member | Rule |
|---|---|
| `id` | a whole number counted by the storage bridge from 1, in a counter of its own (`snapshot_bridge.cpp`, `exchange`) |
| `session_id` | the name the bridge was begun under: the session's id, or a name the worker made for work outside a session (`worker.cpp`, `enroll`, `waveform_control`) |
| `resource` | absent for the enrollment file; `captures` for the pending captures; `speaker_registry`; `recovery:` followed by a digest; `waveform:` followed by a digest. Never `corrections`: the correction list is the carrier's, kept under the operator's confirmation, and a worker that names it is at fault (`snapshot.go`, `workers`; `main.go`, `read`). |
| `action` | absent for a page read; `stage`; `publish` |

By action:

| Action | Further members |
|---|---|
| page read | `offset` (where the page begins); `digest` (true to be told the whole file's digest: the first page and the page that reads back) |
| `stage` | `upload` (a digest that names this publication's staging file); `data_b64` (at most 65536 bytes, base64); `append` (false for the first stage, true after) |
| `publish` | `upload`; `sha256` (the digest of what was staged); and one of `expected_absent` (true: there must be no file) or `expected_sha256` (the digest of the file that was read) |

A page holds at most 65536 bytes. A whole read asks page after page until
`eof`, then one page at the end with `digest` to read back that the file did
not change. A whole publication stages, publishes, then reads the file whole
(`snapshot_bridge.cpp`, `read_owned`, `publish`). One storage operation runs
at a time in the worker, and it waits for one answer at a time.

The carrier turns a request into one file call on the host, on a fixed path
under the plugin's private directory (`snapshot.go`, `target`, `stagePath`,
`call`). It refuses a request whose members do not fit its action, or that
names the correction list, and each is a fault (`snapshot.go`, `valid`,
`workers`; `main.go`, `read`). The carrier's own calls on the correction
list use the request's Go type and never cross this wire (`vocabulary.go`,
`correctionsQuery`).

## 4. Ordering and matching

1. A request's reply carries the request's `id`. Replies are not in request
   order: a held `speech.session.playback_report`, every speaker operation
   and `recording.record` are answered when their work ends
   (`worker.cpp`, `reconcile`, `pump`).
2. The carrier gives the worker `control_ms` to answer a request, two
   seconds by default. A `speech.session.playback_report` is given
   `audio_write_ms` more: the worker may hold one until the audio write it
   counts has ended, and gives that write `audio_write_ms` before it says the
   pipe has stalled, so the worker's deadline comes first. A speaker
   operation or `recording.record` is given the table's time for its storage
   and `control_ms` (`main.go`, `controlWait`; `limits.go`, `playbackReport`,
   `storageOperation`). When that passes, the carrier fails itself; it does
   not answer the host for a request the worker may have seen.
3. There are three counters of `id`: the carrier's for requests, the
   worker's for `settings_request`, the storage bridge's for
   `snapshot_request`. A `settings_reply` answers the `settings_request` with
   its `id` and `session_id`; a `snapshot_reply` answers the
   `snapshot_request` with its.
4. The worker acts only on the answer to its newest `settings_request`. An
   answer to an older one that it did issue is ignored. An answer to one it
   never issued fails the current session with `foreign settings reply`
   (`worker.cpp`, `settings`, `Worker::run`). It remembers the last 32 it
   issued.
5. The carrier serves only the newest `settings_request`. One that arrives
   while another is being read retires that read, which then sends nothing;
   one that arrives while another waits replaces it (`settings.go`,
   `settingsLane`). No number of requests is a fault.
6. An opening session waits `opening_ms` for its `settings_reply` and fails
   when none came. A reply waits `reply_settings_ms` for the answer to its
   refresh; a later answer serves the reply after.
7. The worker acts only on the answer to the `snapshot_request` it is
   waiting for. An answer to an older one it did issue is ignored. An answer
   to one it never issued, or a second answer to the one it waits for, ends
   the worker (`snapshot_bridge.cpp`, `accept`; `worker.cpp`, `Worker::run`).
   It remembers the last 64 it issued. It waits `exchange_read_ms` or
   `exchange_write_ms` for each, inside `whole_read_ms` or
   `whole_publication_ms` for the operation.
8. The carrier serves one `snapshot_request` at a time and holds one more
   waiting. A third is a fault (`main.go`, `read`).
9. The carrier holds at most 64 events it has read and not yet handed to the
   host. One more is a fault (`main.go`, `read`).
10. A fault of the carrier ends the wire: it closes the worker's input. It
    still reads the worker's output to its end and hands on the events in it
    (`main.go`, `read`, `handoff`).

No wait on this wire, and none of the engine's behind it, is a number typed
where it is used: each is a member of the table or is computed from it. The
durations still written in the carrier and in the engine's sources are not
such waits, and tests hold the lists, so that one more cannot be typed
without being listed with its reason (`plugin/native/worker_end_test.go`
for the carrier; `tests/test_runtime_limits.py` for every source of
`runtime/native/session` and of the other directories the worker and its
libraries are built from: the recognizer's, the voice detector's, the
endpoint's, the echo canceller's, the separating recognizer's, the speaker
models', the speech model's resident runtime, and the platform's). In the carrier:
the window in which an operator's confirmation is fresh, ten minutes back and
one ahead; the age at which a staged upload is taken for abandoned, two
minutes, which is over every publication the table can give; how often the
controls' deadlines are looked at, and how often the check of the runtime's
files reports how far it has got. In the worker: how long input is held
before the worker says so (section 6), and how often its loops look again.
In the session: the numbers its header keeps for a caller that states none,
the ranges of what may be stated, and the operator's own settings (the pause
that ends a turn, how long a recording may be). In the libraries: the
numbers the endpoint's gate and the separating recognizer keep for a caller
that states none; what the endpoint's work is asked to take, 250 ms, which is
told to a scheduler and written in a trace and which nothing waits by; how
long speaker evidence that is never stored is kept before it is dropped, ten
minutes; and the length and the step of a frame of the speaker model's
features. A separation's budget is five times the separated audio's length
between its two stated bounds; that ratio is typed, and is not a time.

A drain's idle limit is not held over the limits of what a drain can wait
on, by arithmetic or otherwise: it does not run while such work is in flight.
A drain is called stalled when nothing has moved for `drain_idle_ms`, and
three things hold that clock, each with a limit of its own that is then the
one to speak: an operation on the host's storage (the storage limits), a
model call (`model_call_ms`, by the session's watchdog) and a write of audio
to the host (`audio_write_ms`, by the pipe's deadline). Audio waiting in the
session's queue (`output_take_ms`) waits only behind such a write, because
the worker is that queue's one consumer, and is held by it. Work that ends
moves the drain when it ends: the worker looks at all three on every pass of
its loop, and the drain has `drain_idle_ms` again from the pass that sees the
work ended, so the limit is counted from the drain's last work and not from
a deadline that work ended inside. An abort waits for none of it
(`runtime/native/session/drain_hold.h`,
`tests/test_native_drain_progress.py`). When the idle limit does pass, the
failure names it: `native drain made no progress for 15000 ms, the time the
limits table gives it (drain_idle_ms)`.

`retire_ms` bounds the worker's end whatever its two write limits are: a
write still stalled when it passes is cut off with the sentence of
`retire_ms` and status `72`, so a write limit longer than `retire_ms` is not
reached while the worker is ending (`tests/test_native_output_only.py`).

## 5. The audio pipes

The carrier does not read or write audio. It hands the host's two
descriptors to the worker (1.2), so the frames are the plugin kit's and the
other end of both pipes is the host. The kit's own statement of the format is
`pkg/aiiosdk/audio.go` at the revision `plugin/sdk-source.json` pins.

### 5.1 The frame

A frame is a header of 28 bytes and a payload. Every number is big-endian and
unsigned.

| Bytes | Field | Rule |
|---|---|---|
| 0 to 3 | magic | the four characters `AUD1` |
| 4 | kind | 1, 2 or 3 |
| 5 to 7 | reserved | zero |
| 8 to 11 | stream | 32 bits |
| 12 to 15 | sequence | 32 bits |
| 16 to 23 | start | 64 bits, at most 2^63 - 1: a sample index |
| 24 to 27 | byte count | 32 bits: the payload's length, at most 65536, even, and zero unless the kind is 1 |

The payload is samples of 16 bits, signed, little-endian (`s16le`). The
worker's checks are in `worker.cpp`, `start_threads`.

| Kind | Name | Meaning |
|---|---|---|
| 1 | PCM | samples; `start` is the index of the first |
| 2 | GAP | no payload: the samples before `start` were lost. The kit calls it a discontinuity. |
| 3 | END | no payload: the stream ends at `start`, which is one past its last sample |

There is no fourth kind and no frame that acknowledges another.

### 5.2 Input: host to worker

The worker takes the samples as 16000 a second. They are one channel, or two
interleaved channels (the capture, then the playback reference) when the open
declared `channel_roles`; `start` then counts pairs. The worker does not
resample: the rate an open names is checked for range and not used
(`worker.cpp`, `open`, `input`).

- The first frame of a session may have any sequence. Every later frame is on
  the same stream with the sequence one higher.
- A PCM or END frame's `start` is the count of samples received so far. A PCM
  frame is not empty.
- A GAP frame's `start` is at or past that count, by at most 30 seconds of
  samples. The worker fills the gap with silence and says so
  (`AII_VOICE_GAP`). An enrollment capture and a session with a playback
  reference refuse a gap (`worker.cpp`, `input`, `fill_declared_gap`).
- After END no frame of the session is taken.
- When the open declared `audio.input.stream`, frames on any other stream are
  dropped and counted, and the first is said (`AII_VOICE_FOREIGN_INPUT`). A
  stream number serves one session in a worker's life.
- A frame the session's contract refuses fails that session and not the
  worker (`worker.cpp`, `Worker::run`). A frame the reader cannot parse is a
  transport fault and ends the worker.
- A core that has failed takes no more input and refuses the next frame.
  That refusal is not a fault of the session's. The worker reads the core's
  status when a frame is refused, and when the core carries a failure the
  failure said is the engine's, with the engine's reason, and it is an
  engine failure for the exit status (`worker.cpp`, `Worker::run`,
  `fail_engine`).

Back-pressure: the reader holds at most 64 frames and 32768 samples that the
session has not taken, and reads no further while it does, so the pipe fills
and the host's writes wait. A frame held more than a second is said
(`AII_VOICE_BACKPRESSURE`).

If the input pipe closes while a session is live, the session fails with
`audio endpoint lost; not graceful Finish`, as an engine failure.

### 5.3 Output: worker to host

The worker writes PCM and END frames only. The samples are one channel at
24000 a second. Each reply is a stream of its own: the number the
`speech.session.synthesize` reply gave as `output_stream`. Within it the
sequence counts frames from 0, and `start` is the count of samples written
before the frame. A PCM frame holds at most 32768 samples. The stream's last
frame is an END whose `start` is the count of samples written
(`worker.cpp`, `start_threads`). A reply that was fenced writes no further
PCM. Its END is still written, unless the session was aborted or failed.

Back-pressure: the worker writes one piece of a reply at a time and takes the
next from the core only when the write has ended. A frame that is not wholly
written `audio_write_ms` after its write began, three seconds by default, is
a transport fault: the session fails and the worker ends with status 1
(`worker_io.cpp`, `Pipe::expired`, `Pipe::expiry`; `worker.cpp`, `Worker`
constructor). The `reason` is `native pipe write expired: 3000 ms, the time
the limits table gives it (audio_write_ms)`, whichever thread sees the
deadline first (section 3).

### 5.4 Acknowledgement

Two different things are called an acknowledgement.

- Inside the worker, the thread that writes audio tells the main loop what
  each write delivered (samples, frames, whether the END was written, an
  error). The code calls this an Ack (`worker.cpp`, `struct Ack`). It is not
  on the wire.
- On the wire, the host says what its endpoint has played with
  `speech.session.playback_report` on the control channel (2.2).
  `rendered_samples` may not go back and may not pass what was delivered. A
  `terminal` report is accepted only after the stream's END was written or
  the reply was fenced. A draining session does not end until every reply has
  its terminal report (`worker.cpp`, `admit`, `pump`).

## 6. Diagnostic lines

The worker's standard error is the host's log. The worker writes these lines
there, each a fixed word, one space, and one JSON object. Every object has
`component` (`voice-worker`), `event` and `session_id`.

Each line, its newline included, is handed to standard error by one write
(`worker_io.cpp`, `log_line`). The carrier does not relay the worker's
standard error: it gives the worker its own descriptor (1.2), so both
processes write one stream, and what reads that stream cuts it at each
newline. A line that left in pieces could have the carrier's bytes, or those
of another thread of the worker, between the pieces, and the log would then
hold two lines, neither of them the line that was written. A pipe takes a
write of up to `PIPE_BUF` bytes whole.

| Prefix | `event` | Further members | When |
|---|---|---|---|
| `AII_VOICE_FAILURE` | `failure` | `reason` | the first cause of a failure was recorded, before anything is cleaned up; again when an engine failure follows a session's own; and when the worker ends itself because `retire_ms` or `abort_ms` passed, with the member, its number and what had not ended (`worker.cpp`, `fail`, `fail_engine`, `say_failure`, `retire_passed`, `end_unretired`) |
| `AII_VOICE_SETTINGS` | `session_settings` | `settings` (the settings in force) | an opening session took its settings |
| `AII_VOICE_SETTINGS` | `speech_settings` | `settings` | the voice in force changed at a reply |
| `AII_VOICE_SETTINGS` | `speech_settings_not_taken` | `settings`, `reason` | a change of voice was not taken; said once for each reason, not at every reply |
| `AII_VOICE_SETTINGS` | `opening_waits` | `waiting_for` (`settings` or `open`), `waited_ms` | a session has been opening for `opening_notice_ms`; said once for each of the two things it waits for |
| `AII_VOICE_CORRECTIONS` | `corrections_unreadable` | `reason` | the correction list in a `settings_reply` could not be held |
| `AII_VOICE_BACKPRESSURE` | `input_backpressure` | `received`, `held_ms`, `queued_frames` | an input frame has been held more than a second |
| `AII_VOICE_BACKPRESSURE` | `input_backpressure_cleared` | the same | that hold ended |
| `AII_VOICE_GAP` | `input_gap` | `start`, `samples` | a GAP frame was filled with silence |
| `AII_VOICE_FOREIGN_INPUT` | `foreign_input` | `stream`, `declared` | the first frame of a session on a stream that is not the session's |
| `AII_VOICE_SPEAKER` | `speaker_match_late` | `refers_to` (the final), `limit` (`speaker_match_ms`), `limit_ms` | a final's wait for its speaker was declared over; the `speaker_observation` that follows says `speaker_match_timeout` and carries no number |
| `AII_VOICE_ENDPOINT` | `endpoint_decision_late` | `limit` (`endpoint_decision_ms`), `limit_ms` | the endpoint model's verdict on a pause did not come in time; the turn ends by silence alone and nothing has failed |

The carrier writes two more on the same stream:

- `AII_VOICE_CORRECTIONS` with `component` `voice-carrier` and `event`
  `corrections_unavailable`, and no `session_id`: the store did not yield the
  correction list for an opening session (`vocabulary.go`,
  `sessionCorrections`).
- `AII_VOICE_READY`, written through the kit when the worker's `ready` was
  accepted. It is not JSON: the word, then `event=ready`, `models_loaded=`,
  `accelerator=` and `probe_ms=` with the values of the worker's `ready`
  (`main.go`, `run`; the kit's `ReadyLine`).

Lines with no prefix also reach the log: the carrier's start-up steps, each
one JSON object with `component` `voice-carrier-startup` (`startup.go`); the
carrier's last line, `aii-voice-t3: ` and why it ended (`main.go`, `main`),
and the same prefix before that for a limit of its own that has passed
(`ready_ms`, `worker_exit_ms`, `worker_reap_ms`), said as it passes (`say`);
the worker's `native voice worker: ` and why it could not start; the two
opt-in traces of 1.2; and anything a library in the worker prints, because
the worker points its ordinary standard output at standard error
(`worker_io.cpp`, `protocol_stdout`). Of these the worker's own line is one
write, as its diagnostic lines are. A trace and what a library prints are
written as their writers write them, and can be parted.

## 7. Where the three implementations differ

The line numbers are those of the tree this was written against. They move;
the function named beside each is what to look for.

The first table is computed. For each kind of line it lists the members one
side writes that the other never reads, and the members one side reads that
the other never writes. The document's test recomputes it from the source.

| Line | Written and never read | Read and never written |
|---|---|---|
| request line | none | none |
| `settings_reply` | none | none |
| `snapshot_reply` | none | none |
| worker line | none | none |
| `ready` | `accelerator_scope` | none |
| `settings_request` | none | none |
| `snapshot_request` | none | none |
| `AII_VOICE_LIMITS` | none | none |

1. **The carrier refuses the fixture worker's `ready`; the stand-in accepts
   it.** The fixture worker reports `accelerator` `fixture`
   (`worker_test_models.cpp` line 296) under `backend` `fixture-native`
   (`runtime/native/session/CMakeLists.txt` line 326). The carrier knows
   neither (`main.go` lines 68 to 75, `readinessReport`), so the carrier's
   own tests put a relay between the two that replaces the line
   (`native_fixture_unix_test.go` lines 26 to 50). The stand-in checks only
   that `backend` is `fixture-native` and waits 8 seconds for it
   (`prove_native_worker_transport.py` line 75). The carrier's lane for a
   test double asks for `backend` `deterministic-test-not-real-model` and no
   `readiness` (`main.go` lines 61 to 66), which no C++ worker writes.

2. **The carrier refuses one readiness a worker can report.** A worker that
   was handed a recognizer from outside reports `accelerator`
   `external_recognizer` (`native_c_api.cpp`, `warm`), which names no
   measured placement; the carrier takes only the three pairs of backend and
   accelerator the native worker reports for its own models, with four or
   five models (`main.go`, `readinessReport`), so such a worker never becomes
   ready under a carrier. Nothing reads `accelerator_scope` (`worker.cpp`,
   `Worker::run`).

3. **Each side checks different things about the limits.** The carrier
   bounds what it states at 120 seconds, requires a write to be given a
   read's time and the whole wait to cover a write, and refuses a table whose
   storage operation, or whose wait for a playback report, would outlast the
   host's wait for one call, or whose wait for the worker's exit is not the
   worker's own time to retire and the margin, or in which one of the
   engine's waits does not nest (1.3); its wait for the worker's readiness
   has a range of its own, one second to one hour (`limits.go` lines 213 to
   226 and 290 to 360, `valid`). The worker bounds each member it is handed
   at 600 seconds and checks three other inequalities (`worker_limits.h`
   lines 101 to 122, `validate`). Neither checks the other's rules; the worker
   checks `opening_ms`, `opening_notice_ms`, `audio_write_ms`,
   `control_write_ms`, `retire_ms`, `capture_tail_ms` and the engine's nine
   for range only. The session checks the seven it is handed again, 1 to
   600000, and its recognizer refuses a separation whose most is not less
   than a model call's time. Every table the carrier can compute passes the worker. Neither holds `ready_ms` inside the start its set
   declares to the host: the package's assembly does (1.3). The table is
   stated twice more, and both are held to
   the carrier's source by `tests/test_runtime_limits.py`: the scripts
   restate the carrier's numbers and its arithmetic
   (`scripts/runtime_limits.py`), and the tests keep the table the carrier
   computes from its defaults (`tests/native_limits.py`).

4. **The stand-in starts the worker differently.** It passes seven model
   arguments where the packaged carrier passes none, and three more (a
   backend, a speaker model and a speaker policy's file) where a test asks
   for a worker that can open an enrollment capture. It states the table as
   the carrier does: never one its own environment holds, and the one the
   carrier computes from its defaults unless a test passes its own. A test
   can also ask by name for a worker with no table at all
   (`Worker.NO_TABLE`), which no carrier starts. It passes no liveness
   descriptor and does not remove an inherited
   `AII_VOICE_CARRIER_LIVENESS_FD` (`prove_native_worker_transport.py` lines
   20 to 37). The carrier sets the liveness descriptor itself on Linux and
   macOS, and never passes an argument when packaged (`runtime.go`,
   `packagedRuntime`, `workerCommand`; `handles_unix.go` line 47).

5. **Only the worker checks that request ids rise, and only the carrier
   matches replies by id.** The worker ends on an `id` that is not greater
   than the last (`worker.cpp` line 2394). The carrier keeps unanswered
   requests in a map by `id` and faults on a reply to none of them
   (`main.go` lines 304 to 318). The stand-in takes the next line that is not
   an event or a `settings_request` and asserts that it carries the `id` it
   just sent, waiting two seconds (`prove_native_worker_transport.py` lines
   50 and 85 to 86). It therefore assumes replies come in request order,
   which section 4 says they do not, and it would take a `snapshot_request`
   for a reply: it has no storage at all.

6. **Each side refuses different mixed lines.** The carrier refuses a
   `settings_request` or `snapshot_request` line that also carries `id`,
   `error`, `result`, `event` or `ready` (`main.go` lines 259 to 277). It
   takes a `ready` line that also carries `event` or `id`, and an `event`
   line that also carries `id`, as the first kind and ignores the rest
   (lines 279 to 303). The worker refuses a `snapshot_reply` line that also
   carries `settings_reply` or `id` (`worker.cpp` line 2382, `Worker::run`).
   It takes a `settings_reply` line that also carries `id` and `operation`
   as a settings reply and never answers the request (line 2384). The
   stand-in checks none of this.

7. **Only the worker bounds what it parses.** The worker refuses a member
    name used twice, nesting past 32, more than 10000 values and NUL
    (`worker_json.h` from line 78, `validate`, `parse`), and a line over
    1048576 bytes (`worker_io.cpp` line 211). The carrier bounds the length
    of a line it reads (`main.go` line 252) and nothing else, and does not
    bound a line it writes. The stand-in bounds nothing.

8. **A request's arguments are checked twice for speakers, once for
    recordings, and by the worker alone for sessions.** For the speaker
    operations the carrier refuses arguments over 4096 bytes, any member it
    does not know, a `speaker_uuid` that is not in its exact form, and a
    confirmation that is not recent by the host's clock (`enrollment.go`,
    `validateEnrollment`). The worker checks that the confirmation is there
    and that its `id` was not used before; it does not check its age, ignores
    members it does not know, and takes `speaker_uuid` as a string of at most
    36 bytes at this point (`worker.cpp` lines 1172 to 1184, `enroll`). The
    source says the age is the host's and the carrier's to check
    (`confirmed_acts.h`). Whether the registry checks the form of
    `speaker_uuid` later is not determined. For the session operations the
    carrier checks nothing and passes the arguments through (`main.go` line
    448, `admit`). The stand-in checks nothing.

9. **The stand-in's reader of audio frames checks less than the worker's.**
    The worker refuses reserved bytes that are not zero, a kind other than 1
    to 3, an odd byte count, a payload on a kind other than 1, and a `start`
    past 2^63 - 1 (`worker.cpp` lines 409 to 420). The stand-in checks the
    magic and the length and skips the reserved bytes
    (`prove_native_worker_transport.py` lines 68 to 69). The carrier has no
    frame code. The kit's reader does not require zero reserved bytes or an
    even byte count (`pkg/aiiosdk/audio.go`, `ReadAudioFrame`).

10. **The stand-in answers settings by a rule of its own.** It keeps every
    `settings_request` without `refresh` for the test to answer or not, in
    any order, where the carrier answers only the newest. It answers one
    with `refresh` from the `values` it first sent that session, and never
    with `corrections`, `error` or a reason unless the test writes them
    (`prove_native_worker_transport.py` lines 45 to 49 and 81 to 82).

11. **There are more than three.** The retired Python engine's worker
    (`runtime/plugin_engine/worker.py`, `settings.py`) is a fourth
    implementation, on the worker's side, and tests still start it under a
    development carrier. It refuses a `settings_reply` with any member but
    `id`, `session_id`, `values` and `error` (`settings.py` line 35), and
    that refusal ends its process (`worker.py` line 327); the carrier's
    `reason_code` and `corrections` are such members. It waits a typed two
    seconds for its settings (`settings.py` line 24) where the carrier now
    takes up to `storage_wait_ms`. It does not read `AII_VOICE_LIMITS` and
    never writes `refresh` or a `snapshot_request`. The audio frame has a
    fourth implementation too, `runtime/plugin_engine/audio.py`, which the
    proof scripts for the native engine import.
