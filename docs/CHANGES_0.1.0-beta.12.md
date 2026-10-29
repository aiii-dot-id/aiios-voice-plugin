# What changed in the source for 0.1.0-beta.12

This file covers the source between the tree of 0.1.0-beta.11 and the tree of this release, by subject. The
carrier is the Go program under `plugin/native` that speaks to the host, the worker is the C++ program under
`runtime/native/session` that holds the models, and the fixture worker is the worker built with model-free
stand-ins. 0.1.0-beta.11.1 to 0.1.0-beta.11.4 were maintenance releases of 0.1.0-beta.11 built from a line of their
own; what they repaired that this tree also needed is in the sections below.

Each section says what was wrong or what was missing, what the tree holds now, and how that was shown.

This file describes the source. Which runtime sets, speaking libraries and carriers the 0.1.0-beta.12 package
carries, and on which machines that package was qualified, are stated in the release notes published with it.

## Faults repaired

### A reply that replaces one being made is spoken after it, not refused

**Was wrong.** A host cancels the reply a newer one replaces, and sends the newer one as soon as that cancel is
admitted. The core admitted a reply only behind one that had retired, and a cancelled reply retires only when its
model call returns. So the newer reply was refused ("prior synthesis has not retired") and given as text.

**Now.**
- `Session::synthesize` admits a reply behind a cancelled one. The loop takes it when the cancelled one retires, so
  one speaking model never makes two replies at once.
- A cancelled reply the loop never took retires at once.
- A stop alone still holds the next reply.
- The cancelled reply's late chunk never reaches the output, and its END comes before any of the newer reply's
  audio.

**Shown.** Session tests:
- Without the repair, the replacement is refused with the production message.
- A cancelled model hands back the chunk it was computing, and not one sample of it reaches the output.
- The older reply's END is delivered first.

Through the installed host on macOS Full, a long reply replaced at once 20 times was refused 20 times on
0.1.0-beta.11 and spoken 20 times with the repair.

### Text a speaking language's removals leave empty is said by no audio

**Was wrong.** French removes « » " ( ) [ ] and other characters its model never saw. A reply segment made of
removed characters only completes in the native adapter with no audio, as documented. The core took no audio as
the model's fault and stopped the session.

**Now.** `Synthesizer::silent()` says the segment started last has, by the backend's own rule, nothing to say. The
native adapter answers it from its table, and the model channel carries it. The core takes no audio as natural
completion only then; a model that ends words it was given with no audio is still a fault.

**Shown.** `native_models_injection_test`, through the adapter and the core behind the C API with model doubles:
- the silent reply's END and receipt are taken, and the session goes on;
- the removed text never reaches the model;
- a model's empty answer still faults.

The test fails without the repair. `model_channel_test` shows the answer crossing the channel.

### An engine failure ends the worker, which serves no other session on a broken model

**Was wrong.** After an engine failure (a model failed inside a reply, a model call passed its deadline, a request
failed in the core), the worker said the failure and ended the session but went on serving. A speaking model keeps
its fault across a reset, so the next session in the same language failed as well.

**Now.** An engine failure begins the worker's retirement. While it ends, it answers status and refuses every
other request with the reason. A failure that belongs to one session still leaves it serving.
`docs/NATIVE_WORKER_WIRE.md` (1.5) states it.

**Shown.**
- `test_native_fault_scope`: with the input left open, the worker exits by itself with status 1. Without the
  repair it was still serving after 20 s.
- `test_native_model_progress`: a model call past its deadline retires the worker, and the retiring worker
  answers status and refuses an open.

### The last word is heard when the input ends at it

**Was wrong.** A recording that ended at its last word lost that word's end ("before the rain com"). The streaming
encoder had no lookahead at finish, and the loss depended on where the input ended against the encoder's chunk.

**Now.** At finish the multitalker microphone gives its streaming encoder one whole chunk of silence (112 feature
frames, 1.12 s) as lookahead, and nothing else. The diarizer, the speaker evidence and the refinement never see it,
and no update's extent passes the real samples.

**Shown.** With real models on macOS, a reply cut every 20 ms around its end is heard to its last word at every
end point. beta.11's engine lost "rain comes" over 80 ms of end points. The final's wait was not longer
(1.4-1.8 s against 1.6-1.9 s).

### A forced cleanup on Windows no longer ends the carrier with the worker

**Was wrong.** On Windows the worker ran in the carrier's own job, so a forced cleanup ended the carrier as well.
Its final events were lost, and a worker left suspended could hold a bare wait for ever.

**Now.**
- The carrier starts the worker suspended, puts it in a job of its own nested under the carrier's, and only then
  resumes it. A forced cleanup ends only the worker's job; the carrier reaps the worker and flushes its final
  events.
- A worker the carrier could not put in its job is reaped within the limits table's `worker_reap_ms`, and a failed
  termination is reported, not ignored.
- The cancelled, partial-write and closed-reader pipe paths are carried with portable fixtures.

**Shown.** Windows tests start a worker suspended and never resume it: the carrier returns within the reap time
with both errors, the control ends it with status 73 and it is reaped at once, and a worker placed in its own job
is resumed and runs to its own end. The wire document's exit-status rule reads the worker's own job.

### The Windows session project and fixture worker build with MSVC again

**Was wrong.** The session project's tests ignored SIGPIPE, which Windows has not, and the worker read its pipe
handles and `AII_VOICE_LOG_CODES_ONLY` with `getenv`, which `/W4 /WX` refuses. A parameter of the accelerator line
hid the speaker backend's name (C4459).

**Now.** SIGPIPE is ignored only where it exists; on Windows those variables are read with `_dupenv_s`, as the
worker's others are; the parameter is `uid_backend_name`.

**Shown.** The session project and the fixture worker build under `/W4 /WX`.

### The portable echo patch applies with LF line endings on every builder

**Was wrong.** On a Windows builder whose Git converts line endings, the build-tree copy of the patched AEC3 source
was written with CRLF endings, its digest no longer matched the pinned patched bytes, and the echo build stopped.

**Now.** The patch is applied with LF line endings whatever the builder's Git settings.

### The speech separator's threads block between kernels again

**Was wrong.** A change made the separator's threads spin while it ran. On Windows, two of the three desktop sets
then lost speaker separation on recorded two-speaker mixtures; its effect on speed had never been measured.

**Now.** The separator runs with 0.1.0-beta.11's options.

**Shown.** Every Windows run with those options separated every mixture on all three sets.

### Tool schemas are portable across providers

The speaker operations' input schemas use only the forms every model provider the host supports accepts, and a
test holds every operation schema to them (`tests/test_tool_schema_providers.py`).

## Speaker voiceprints

### Voiceprints of people never enrolled expire, and a switch stops them being made

**Was missing.** A voiceprint made for a person who never enrolled was kept without limit of time, and nothing
turned their collection off.

**Now.** Two settings:
- `anonymous_expiry_days` (default 30, from 1 to 3650): once that many days have passed since such a person was
  last heard (the voiceprint made, or accepted as a match), their speaker is deleted with its voiceprint and
  labels. Linked speakers expire together, from the latest day either was heard, and are kept while any is
  enrolled. A name given by association is not enrollment. Transcripts are never touched.
- `anonymous_profiles` (default on): off, no voiceprint of a person never enrolled is made, matched or kept fresh,
  and no recording is held to corroborate one; those already kept still expire when due.

The rule is applied whenever the speaker files are read: before an observation is matched and before
`speaker.buckets`, `speaker.associate`, `speaker.forget` and `speaker.link`. `speaker.buckets` says the rule in
force and, for each such speaker, `heard_on` and `expires_on`; a call that deleted any lists them in
`expired_speaker_uuids`. The worker's log gives the count, never whose. Because listing can delete,
`speaker.buckets` is declared a local write.

The days are kept in a file of their own beside the speaker registry (`uid/speakers-heard.json`, UUIDs and days
only), and the registry's format is unchanged, so an earlier version rolled back to still reads its speakers. A
speaker kept before this rule existed is counted from the day the rule first sees it, so nothing is deleted at
upgrade.

**Shown.** With synthetic vectors only: expiry, refresh, linked groups, enrolled and removed enrollments and
canonical bytes in the codec; deletion at expiry, an enrolled speaker kept, the period changed by the setting, a
match moving the day, the switch, and what `speaker.buckets` says in the store; every registry written held to the
earlier format; and every operation that can expire a speaker held to a write.

### Recovery archives are listed and deleted

`speaker.reset` with `recovery` sets aside the speaker files the runtime cannot read as recovery archives, which
hold voiceprints and names, and until now nothing listed or deleted them. `speaker.recovery_list` lists them with
their size, what each holds and when it was saved, never their contents; `speaker.recovery_delete` deletes one by
its exact digest after operator confirmation. The carrier serves both from the private files.

## Design upgrades in the package

### Each large model is loaded once and shared

- The hearing's large models are loaded once and shared, each behind a lane, and each session's hearing is its
  own: the multitalker encoder's weights, the diarizer and the ONNX separator, whose sessions keep their own run
  options and cancel.
- The speaker-identification model is loaded once and shared. Each session's speaker has its own policy,
  snapshot, observers, evidence and cancel; a second handle cost about 514 MB on macOS Full.
- The speaking model's weights are uploaded once per device and shared, where a speaking library is built from
  this source.

In the resident worker one session runs at a time, so this changes the cost of nothing a session does. It is what
lets one engine serve several sessions at once.

### The Vulkan shaders' optional arithmetic is off

The worker turns off ggml's cooperative-matrix, integer-dot-product and bfloat16 Vulkan shaders, as it already
turned off half precision, so a speaking library built with a shader compiler that can make them speaks as the
shipped libraries do. This takes effect where a speaking library is built from this source.

### The ready line names the providers the components registered

**Was.** The ready line was one fixed string per speaking backend ("cpu_vulkan", "cpu_metal"), so Linux Full, which
registers CUDA for the recognizer, said nothing of CUDA.

**Now.** The line names "cpu" first, then each registered provider (cuda, directml, vulkan, metal, coreml) in
`accelerator.json`'s words, from the speaking backend, the recognizer's execution report and the speaker model's
backend.

### The engine's own session checks (`plugin/native/validation.json`)

A host can prove a plugin on the machine in front of it, at install and at every start, with checks the plugin
writes. This engine's two checks:
- "hears a reminder to its last word": its own voice saying a reminder, cut exactly where 0.1.0-beta.11's engine
  lost "rain comes";
- "speaks a reply faster than real time": 3,000 ms of audio within 3,450 ms of the open.

The package carries `validation.json` and the recording it names. A check's audio enters no recording window, and
its speaker observations read and keep nothing. A momentary failure says a retry may pass. On a machine whose
speaking set is slower than real time, a host that runs the checks passes over that set and the next set serves.

### The engine's log carries keys, codes and numbers, never an identity's values

With `AII_VOICE_LOG_CODES_ONLY=1`, and in a session process always, the worker's log names a setting by its key
and a failure by its code. It never writes an identity's values: names, labels, transcripts, settings' values.

### One Go version, named once

The carrier's Go is named once, in the `go` line of `plugin/native/go.mod`, where every script reads it
(`scripts/go_toolchain.py`).

### The carrier builds against a public SDK release

The carrier builds against the public Plugin SDK release 0.1.16-sdk.1 (`plugin/sdk-source.json`). A public SDK
release carries no tests or vectors, so the tree carries the vectors the source was qualified with under
`tests/vectors/`, read through `scripts/sdk_vectors.py` only for the pinned revision, and the pin test holds the
pinned SDK's code to the code those regressions ran against (`scripts/sdk_code_digest.go`).

### Example names are neutral

The correction and enrollment examples and the tests use neutral names (Maple, Alex, Ivy, Fern), never a person's.

## In the source, not used by this release's carrier

**The engine split.** The tree holds the engine that serves every identity on a machine from one set of models:
- a model server, with model channels for speech, hearing and the speaker, over pipes on every system;
- a session process per channel, which does not warm;
- admission by the engine's own measured costs (`ENGINE_BUSY` with a retry-after, the first session always);
- the Go engine core that runs them;
- a lost server and a hung stream are bounded.

The carrier still runs the resident worker: the engine core is wired to a host only through an engine-serving
entry point that neither the Plugin SDK nor AII OS offers yet. The pieces were shown by their own tests and by
measured runs with real models on the three systems.
