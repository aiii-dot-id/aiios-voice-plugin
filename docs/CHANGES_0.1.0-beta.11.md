# What changed in the source for 0.1.0-beta.11

This file covers the source between two trees: the tree of the release commit of 0.1.0-beta.10,
which was built and never published, and the tree of this release. The repository carries
everything between them as one commit, and this file stands in for the per-change history. It is
arranged by subject, not by change: each section describes the tree as it is in this release and
what was wrong in the earlier tree.

Each section says what was wrong, what the tree holds now, how that was shown, and what was not
shown. What no section shows (Windows, what ran on macOS and what did not, sessions with real
models, a person's speech) is gathered once in the last section and not repeated in each.

Three words are used throughout. The carrier is the Go program under `plugin/native` that speaks
the plugin kit's protocol to the host. The worker is the C++ program under
`runtime/native/session` that holds the models. The fixture worker is the same `worker.cpp` built
with model-free stand-ins, which most tests here drive.

## A saved voice is the next reply's, not the next session's

**Was wrong.** A voice preset was read when a session opened and at no other time, and a page
keeps one session open for as long as voice is on. A voice saved in the settings changed nothing
that could be heard until voice was turned off and on. The setting's own text said "Applies next
session" and nothing else did.

**Now.** The voice, its variation and its seed are taken at the first segment of each reply and
never inside one (`session.h`: `speech()`, `speech_pending()`, `speech_unchanged()`,
`speech_state()`; `c_api.h` and `native_runtime.def`: `aii_voice_set_speech`,
`aii_voice_speech_pending`, `aii_voice_speech_unchanged`, `aii_voice_speech`). `speech()` refuses
at once what the backend would refuse (`Synthesizer::check`, which touches no model;
`native_models.cpp` answers it for the native speaking model from the files on disk alone) and a
change of language, which is another model and stays a new session's. A change refused where it
is applied, such as a preset removed in between, leaves the voice in force, is said in the state,
and never costs the reply.

As a reply is admitted the worker asks its carrier for the settings in force (`settings_request`
with `refresh`) and tells the core an answer is coming. The reply's first segment waits for it,
bounded by the table's `reply_settings_ms` (150 ms), and a later answer serves the reply after.
For such a request the carrier reads the settings alone, inside a read's time, with no correction
list. The session's readback names the voice in force, and the `AII_VOICE_SETTINGS` line says
`speech_settings` when it changes and `speech_settings_not_taken`, once per reason, when a change
is not taken. The descriptions of the three speaking settings say "Applies from the next reply";
a language and every hearing setting still say next session.

**Shown.** `reply_voice_test.cpp` (`native_reply_voice_contract`): the voice asked for is the next
reply's; never a part of the reply being spoken; a reply waits for settings still being asked
for; what cannot be taken never costs a reply. `tests/test_native_reply_voice.py`, against the
fixture worker, whose speaking model holds a second voice told apart by the length of its audio:
`test_a_saved_voice_is_the_next_replys_and_every_later_one`, four changes that cannot be taken
(`test_a_change_that_cannot_be_taken_leaves_the_voice_in_force_and_is_said_once`),
`test_a_reply_does_not_wait_past_its_bound_for_settings_that_do_not_come` and
`test_a_reply_asks_for_the_settings_alone`. In the carrier,
`TestARepliesQuestionIsTheSettingsAloneInsideAReadsTime`. 23 faults were planted one at a time
(13 in the session, 7 in the worker, 3 in the carrier) and every one fails a test.

What a reply owes to a longer reply spoken before it in the same session was measured with real
models, in trials made before this tree's last change (last section), on the processor and on
the graphics path of each system that has one. A reply that no longer reply precedes is its
reference sample for sample on every set measured. After a longer reply, in two voices on each:

- on the Linux Small-CPU set it differs by a rounding: at most 4 units of 16-bit PCM, 85 dB
  below the speech or better;
- on the Windows Small-CPU set it differs by at most 45 units, 61 dB below the speech (74 and
  61 dB);
- on Linux through Vulkan it is identical;
- on Windows through Vulkan it differs by at most 24 units, 67 dB below the speech (80 and
  67 dB);
- on macOS through Metal it differs by at most 77 units, 58 dB below the speech (58 and 65 dB).

The two Small-CPU sets both plan the graph on the processor and do not measure alike; neither do
the graphics paths. The engine sources are not alike either. The source kept for the Linux
library holds a change in `session.cpp` for a graph that is not planned on the host, and one of
the two changes of `capacity-history.patch`; the sources the Windows and the macOS libraries
were built from hold neither (`runtime/native_pocket/engine_overrides_linux/NOTICE`; the section
on the engine's files). That the measured differences come from those differences of source was
not put to a test.

**Not shown.** The time a reply's question adds to its first sound is not measured. The
measurements above are of trials at commits before this tree's last change, not of this
release's own sets.

## An engine failure is no longer lost

**Was wrong.** A failure the core raises on one of its own threads reaches the worker in the
core's status, which the worker reads twice in each pass of its loop (`pump` in `worker.cpp`). It
looked for the failure after the first reading only, and it ends a session on the second. A core
that failed and retired between the two was released with its failure unsaid. With a session's
own failure before it, the log had the session's line alone and the exit reported success. With
none, the session ended as "completed": no failure event, nothing in the log, and an exit of 0.
This held for any failure the core raises on its own threads, a model call past its limit among
them.

A core that has failed takes no more input and refuses the next frame. That refusal was said as
the session's own fault: the failure event read "input admission closed", and the engine's reason
was only the log's second line. The host shows the event's reason to the person using it.

Each diagnostic line left the worker in three writes. The carrier does not relay the worker's
standard error: it gives the worker its own descriptor, so both processes write one stream, and
what reads that stream cuts it at each newline. A line that left in pieces could have the
carrier's bytes, or those of another thread of the worker, between the pieces, and the log then
held two lines, neither of them the line that was written.

The two tests of an engine failure beside a session's had faults of their own. They took
`synthesis_start` to mean the synthesizer was inside the reply. It says the reply was admitted: a
reply cancelled before its synthesizer is inside it ends cleanly, and no engine fails. One of the
two fed speech beside the reply, which is a barge-in: it cancels the synthesizer by itself, and
the engine's failure could then come before the session's. And the tests' stand-in for the
carrier closed the control channel and the audio input back to back: the audio's end could be
read first, and a worker closed with a session open then said its audio endpoint was lost and
exited 1.

**Now.** Five lines stand after the pump's second read of the core's status: a failure that
status carries is said there (`fail_engine`), before the session is ended on it.

When a frame is refused the worker reads the core's status, and when the core carries a failure
the failure said is the engine's, with the engine's reason, and it is an engine failure for the
exit status. Otherwise the refusal is the session's, as before.

Every line the worker itself writes to its log, the line of a start that failed included, goes
through one writer (`log_line` in `worker_io.cpp`), which hands the line, its newline included,
to standard error by one write. A pipe takes a write of up to `PIPE_BUF` bytes whole. A trace,
and what a library prints, are written as their writers write them and can still be parted.

The fixture has a seam that makes the order the only one (`worker_test_models.cpp`).
`AII_FIXTURE_RESET_FAILS_AT` names the place on the worker's main loop where the fixture
synthesizer's reset fails: `last_status`, between a pass's two reads of the core's status, where
the pass stays until the core has retired as well; or `input`, with a frame in hand before it is
fed. The seam says in the log that it made the order, and a test whose run did not get that order
fails where it would otherwise pass. The two calls in `worker.cpp` (`before_last_status`,
`before_input_frame`) stand under the macro that only the fixture target defines, and what they
call is defined only in the fixture's models: the seam is in no production build.

The two tests wait for the reply's first audio, which the fixture's "Break." reply now speaks
before it holds, and the session's fault in the first of them is a gap too long, which needs no
speech before it. The stand-in (`close` in `scripts/prove_native_worker_transport.py`) closes the
control channel, waits for the worker's exit, and only then closes the audio input, as it is
behind a carrier: the carrier holds no writing end of the worker's audio input, and that pipe
ends when the host closes its end and not before. `docs/NATIVE_WORKER_WIRE.md` states each of
these. The closeout's floor rose by the seven new tests.

**Shown.** In `tests/test_native_fault_scope.py`, for the two reads:
`test_an_engine_failure_between_a_passs_two_reads_of_the_core_is_said_after_a_contained_failure`
and `test_an_engine_failure_between_a_passs_two_reads_of_the_core_fails_its_session`, each of
which runs one of the two tests that were already there
(`test_an_engine_failure_after_a_contained_session_failure_fails_the_exit`,
`test_the_same_engine_failure_alone_fails_the_exit`) with the seam at `last_status` and requires
the seam's line in the log. For the refused frame:
`test_input_refused_by_a_core_that_has_failed_is_the_engines_failure`, with the seam at `input`:
the failure event's reason is the engine's and the exit is 1. For the seam:
`test_the_engine_failure_seam_is_fixture_only`. For the log:
`test_each_line_of_the_log_is_one_write`, which gives the worker a datagram socket as its
standard error, so that each write arrives as one record, and requires every record to be one
whole line; and `test_the_worker_has_one_writer_of_its_log`. For the stand-in:
`test_a_worker_closed_with_a_session_open_ends_as_it_does_behind_a_carrier`.

On Linux, at the tree with the correction: the fixture build and a development build, 57 of 57
native tests; the carrier's Go tests with the fixture worker; and the 16 modules that drive the
fixture worker, with the wire document's and the limits' tests, 336 cases, none skipped. The
seven new tests passed 25 runs each.

Measured on Linux. The two tests as they were before the correction, pinned to one processor:
41 of 300 runs of one failed and 4 of 300 of the other; with the window widened on purpose in a
copy, 10 of 10 in each of five cells. Corrected: 0 of 3000 runs plain, 0 of 1000 pinned, and 0 of
10 in every widened cell. Each part of the correction, taken out again in a copy, is caught 10 of
10 by its own test: the lines after the second read by the two tests that force the order, the
masked reason by the test of input refused by a failed core, and one write per line by the test
that reads the log as records. strace shows one write for each line.

On macOS with Apple clang, on an earlier state of the tree (before the limits' last rounds) with
this correction applied: the module's 27 cases passed, and with the lines after the second read
removed exactly the two tests that force the order failed. The closeout gate rehearsed there
failed once at its end with "audio endpoint lost" before this correction, and passed whole with
it.

**Not shown.** The stand-in's old order of closing was not made to fail on Linux (0 of 25 runs);
the macOS run is the evidence that it can, and
`test_a_worker_closed_with_a_session_open_ends_as_it_does_behind_a_carrier` shows only that the
new order ends as it does behind a carrier. `test_each_line_of_the_log_is_one_write` is skipped
on Windows, where the writer is another call, and that call was not run. What a host does with an
engine failure that used to be silent was read in the host's source, not run.

## An abort's deadline is the first abort's, and an open has a limit

**Was wrong.** Every abort the worker took set the abort's deadline again: a host that sent abort
again inside the five seconds kept a session that would not retire, and the process holding it,
for as long as it went on sending. A failure after an abort did the same once. A session whose
open never returned (a model's load that hangs, when the speaking language changes) was looked at
by no deadline: aborted, it stayed "draining" for good, the abort answered "accepted" and every
later open was refused; left alone, it stayed "opening" for good. An enrollment capture whose
preparation never returned had the same shape. A session that was slow to open said nothing about
why.

**Now.** An abort's deadline is set by the first abort and by nothing after it. An open has the
table's `session_open_ms` (60 s), after which the engine says "session open did not return in N
seconds", waits the abort's time for it to come back after all, and ends with status 72, as an
aborted session whose core will not retire ends. An aborted open that has not returned by the
abort's deadline ends the same way, and so does a capture whose preparation never returns. Before
it ends so, the worker says in its log what had not ended, with the member's name and its number:
"... had not ended N ms after its abort, the time the limits table gives it (abort_ms)". The
abort's time (`abort_ms`, 5 s) and a capture's close (`capture_close_ms`, 45 s) are members of the
table; they were typed in the worker.

A session that has been opening for the table's `opening_notice_ms` (1.5 s) writes
`AII_VOICE_SETTINGS` `opening_waits` once, with what it waits for (its settings, then its open),
and its status says so for as long as it is true (`"opening"`: `waiting_for`, `waited_ms`).

**Shown.** `tests/test_native_abort_while_opening.py`, against the fixture worker, whose open can
be held behind a gate: an abort before the settings arrive; aborts sent again; an abort right
behind the settings; an aborted session that will not retire; an abort sent again does not move
the deadline, for a session and for a hung open; an aborted open that never returns; one that
returns inside the abort's time; an open nobody aborts; a failure after an abort; and an opening
that waits for its open says so. The notice for an opening that waits for its settings is held by
`test_an_opening_that_waits_for_its_settings_says_so_once_and_its_status_says_so_while_it_is_true`
in `tests/test_native_fault_scope.py`. Six faults were planted for the abort and the open, one at
a time, and every one fails a test.

**Not shown.** The open that never returns is the fixture's, held behind its gate; no real
model's load was involved.

## An open that is refused leaves the session before it as it was

**Was wrong.** The worker's open read the capture report (`audio.input.processing`) into the
worker's own state as soon as it had checked it, and only then read the input's channel roles,
which can still refuse the open: an order of roles this engine does not serve, or a playback
reference in a build made without that frontend. A refused open is to leave no session and change
nothing, and it left its own capture report in the status of the session that had ended before
it.

**Now.** In `worker.cpp` the check returns what the session will show (`checked_processing`) and
changes nothing; the worker keeps it where it takes the rest of the open, after the last thing
that can refuse.

**Shown.** `tests/test_native_output_only.py`,
`test_a_refused_open_leaves_the_session_before_it_as_it_was`: the host's first open with its
whole capture report, drained; then an open with another report and its roles in an order no
build serves, refused with "unsupported input channel roles" and no event; the first session's
status shows its own report, its id and its lifecycle; and an open that names the same stream
number is then admitted, so the refused open took no stream. With the report kept before the
roles are read again, planted in a copy of the tree with the fixture rebuilt, that test fails.

**Not shown.** The refusal of a playback reference by a build made without the frontend takes the
same path and was not driven separately.

## A status request never reads a speaking model that is being replaced

**Was wrong.** On Linux a `speech.session.status` request read the speaking model's pointer with
no lock (`NativeModels::tts_execution_info`) while a session opening on another thread could be
inside `select()`, where the model is released and another is created. In the window with no
model the readback failed ("TTS execution readback failed"), which the worker takes as an engine
failure, not a session's. In the window where the model was being released it read freed memory.

**Now.** In `native_models.cpp`, for the Linux desktop, the description is asked of the model by
its owner alone, each time one becomes resident (after the first load, after a successful select,
and on select's failure path after the restore attempt), and kept as text under its own small
mutex. A readback takes that copy: never the model pointer, and never the owner's lock, which a
load holds for as long as it takes. During a language change the readback describes the model
before it; with no model resident it fails with the same sentence as before. `start`, `next` and
`reset` still use the model outside the owner's lock, as they must for a cancel to get in; a
comment states the condition that orders them with every write.

**Shown.** `native_models_injection_test.cpp` (`native_model_injection_contracts`) uses doubles of
the library's calls that keep released models' memory and count calls, through the two entry
points the worker uses on two threads. The open to another language is stopped inside the release
and inside the create, and in each a readback returns within its bound, describes the previous
model and makes no call into the library. After the change it describes the new model; a load
that is refused restores and is described; with neither loaded the readback fails with the old
sentence; forty unheld changes run beside a reader, every answer whole and never older than the
last; and the model is asked exactly once each time one becomes resident. On the code before the
change the test fails naming both windows. Of six faults planted in copies of the file, five are
caught by the test; the sixth, the copy read without its mutex, only by ThreadSanitizer, which
reports the race for it and none for the corrected code in three runs.

**Not shown.** The readback against the real library: the test uses doubles of its calls. On
macOS and Windows the description is an immutable string and this path does not exist.

## Late storage is said as late, and what it left undone is done later

**Was wrong.** A read or a publication the host did not answer in time reached the session as
"enrollment_unavailable", which reads as a missing or unreadable enrollment and points at the
wrong problem. A session that was ending cleanly was failed ("native drain made no progress for
15 seconds") while a speaker's observation was still inside the storage's own limits; an
enrollment capture's close had the same shape, and the model watchdog counted the registry's
storage as part of the speaker model's call.

An observation is two things: an answer for its final, and what the speaker files learn from the
utterance. When the host's storage was late both were lost together. A voice heard while storage
was slow taught the files nothing; and a profile whose two recordings had just been matched was
not stored and, because a recording is never offered twice as its own confirmation
(`runtime/native_uid/profile_admission.h`), could not be matched again from the same two.

**Now.** The carrier marks a query the host did not answer in its time with the reason
`HOST_STORAGE_NO_ANSWER` (`snapshot.go`). The worker's storage bridge raises that as
`StorageLate`, a refusal of its own type, on a read, on a stage (nothing was published) and on a
publish (the outcome is unresolved, as before, and it says it was late). The model's reader and
the registry's observer return BUSY for it and FAILED for everything else, and `c_api.h` says
what BUSY means from a storage callback. The session's observation then carries the reason
"speaker_storage_late" and not "enrollment_unavailable".

A session's end is not failed by storage that is inside its own limits. The model watchdog covers
the speaker's inference only: the identifier says when its inference is over (`model_part_done`,
`session.h`) and the registry's storage that follows runs under the storage's limits; an
identifier that never says so is watched for the whole call, as before. A drain's deadline is the
table's `drain_idle_ms`, and storage in flight holds it: the rule is one unit, `DrainHold` in
`drain_hold.h`, which the section on the limits describes, because a model call and a write of
audio hold a drain by the same rule. The worker looks on every pass, for a conversation's drain
and for a capture's close: storage that has ended moves the deadline when it ends, storage in
flight holds a deadline that has passed, and an abort waits for neither.

What late storage left undone for the speaker files is kept and done when storage answers again
(`speaker_registry_store`). When an observation's reads are late, the recording is kept and
observed again as the observation it was (its own session and utterance). When its publication is
late, the change is kept and published again only after reading the file back: taken as done if
the file is already what the change made it, published if the file is still what the change was
made from, dropped if it has since become something else. Both are bounded (four each), done
behind the next observation whose own storage answered and never ahead of its answer, and leave
the tracks of the utterance being observed as they were. They belong to observations only: a
change made by a confirmed operation that was late is still refused and is never done later here.

**Shown.** `snapshot_bridge_test.cpp`: `a_write_waits_a_writes_time` and
`late_storage_is_said_as_late`. `speaker_test.cpp`:
`late_storage_is_not_an_unavailable_enrollment`. `model_progress_test.cpp`:
`speaker_storage_outlasts_the_model_deadline`. `drain_hold_test.cpp`
(`native_drain_is_held_by_work_in_flight`), with an injected clock: storage that ends 4 s into a
15 s deadline gives a deadline of 4 s plus 15 s, and no second renewal at expiry. In the carrier,
`TestAQueryTheHostDidNotAnswerInTimeSaysSo`: a query left unanswered says so and a refused one
does not.

`capture_enrollment_test.cpp`, `late_storage_is_kept_and_finished`, runs against a stand-in host
that can be late at a read, at a stage, and at the publish with the file changed all the same or
not. The kept recording completes a profile behind the next answer; a change late at a stage is
published at the next chance; late at the publish, it is read back and not published again if it
had landed, and published if it had not; late twice, it is kept for the third time; the file
moved on, it is dropped and the file is untouched; a confirmed change is not kept; the bound
holds; two tracks of one utterance still cannot both be given one speaker after a kept recording
was finished between them. Eleven faults were planted one at a time and eleven caught.

**Not shown.** No test drives a drain with storage in flight through the worker itself: the
tests' stand-in for the carrier has no storage, which is why the rule is a unit with its own
test. That a real session's end is held by a real speaker's storage needs an installed plugin
with the host's storage slowed on purpose, and was not done. Nothing here ran against a real
host's storage. What is kept lives in the worker's memory: a worker that ends before storage
answers again loses it, as before.

## The newest settings request wins, and a refused opening says what failed

**Was wrong.** The carrier read the worker's settings requests in order from a channel of one,
though the worker answers only its newest. The read of an aborted session went on to its limit,
the next open waited behind it and was given up ("settings preparation timeout"), and a third
request inside the same wait found the channel full, which failed the carrier ("private settings
capacity exhausted") and with it the engine.

An opening whose settings did not come was refused with one sentence for three different
failures, and a fourth (no word from the carrier at all) was called "settings preparation
timeout". Nothing in the log said which settings a session had been given, so someone who heard
another voice than the one saved could not tell whether the session predated the save.

**Now.** A `settingsLane` (`plugin/native/settings.go`) takes the channel's place: it holds the
newest request not yet begun and the context of the read in flight. A newer request replaces the
waiting one and retires the read in flight, which then says nothing to the worker, since the
worker would pass its answer over. The capacity fault is gone with the channel.

The carrier answers a failed settings read with which of three it was, in `reason_code`, and the
worker has one reader of it (`settings_failure` in `worker.cpp`). A refused opening says "host
settings: no answer from the host in time", "host settings: the host refused the read" or "host
settings: the host's answer is not settings" for the carrier's three reasons, and "host settings:
no answer from the carrier in time" for the worker's own bound. A code the worker does not know,
or none, is "host settings unavailable". The same reader serves a reply's question for the voice
in force. Each session writes one `AII_VOICE_SETTINGS` line with its effective settings.

**Shown.** `settings_lane_test.go`. `TestTheNewestSettingsRequestWins` sends five requests
through the real reader while the first read is with a host that does not return: that read's
context ends, nothing is written for it, the next read is the fifth request's and its answer is
the first line the worker gets, no request between is read, and the carrier has not failed.
`TestSettingsRequestsOneAfterAnotherAreEachAnswered` and `TestASettingsRequestIsTakenOnce` stand
beside it. Of eight faults planted in the lane seven fail a test; the eighth, a finished read's
cancel left in place, changes nothing that can be observed.

`TestASettingsFailureSaysWhichOfThreeItWas` holds the carrier's side, and
`tests/test_native_fault_scope.py` the worker's
(`test_a_settings_read_that_failed_says_which_of_three_it_was`,
`test_each_session_says_in_the_log_which_settings_it_was_given`). Each side's own test cannot
show that the two use one member name, so `plugin/native/settings_reason_unix_test.go` runs the
real carrier with the fixture worker (`TestAFailedSettingsReadReachesTheSessionAsTheWayItFailed`):
the read fails three ways (the host answers an error, answers something that is not settings,
answers nothing), each session ends with its own sentence, and a fourth session then opens. With
the worker reading the reason under another name, or the carrier writing it under another name,
each planted in a copy of the tree, all three cases fail with "host settings unavailable".

**Not shown.** The no-answer case has half a second between the carrier's wait and the worker's;
it passed every run made and was not run under heavy load.

## The limits: one table, stated, nested and handed to the worker

**Was wrong.** The time limits between the worker, its carrier and the host were numbers typed
where they were used. The carrier gave the host 1.5 s for anything it asked, a durable write as
much as a read. The worker waited 2 s for the carrier's answer to one exchange, 10 s for a whole
read, 30 s for a publication and 2 s for a session's settings. A speaker operation had 45 s,
beside reads of 10 s each that could sum past it, and a drain had 15 s. On a disk that was slow
for a moment the plugin gave up: a session was refused at its opening, a speaker was reported
unavailable, a profile was not stored. The limits did not nest either: an outer one could pass
while everything inside it was still within its own.

Every other wait on this road was typed too, each a limit an installation could not state and a
sentence that named nothing when it passed. In the worker: five seconds for an abort and
forty-five for a capture's close, no limit at all for an open, two seconds for an enrollment
capture's last frames, three for a write to either pipe, and its own end as two fives in a row,
five seconds for its main loop and a fresh five for its threads, while its carrier killed at
five; when it gave up it exited with status 72 and no word. In the carrier: two seconds for every
control request, five for its worker's exit and five more after a kill, two for the host lane's
flush at its end, forty for the worker's warm inference, and ten for each of its own storage
operations. In the session library: thirty seconds for a model call, three for a conversation's
last frames, fifteen for synthesized audio to be taken, fifteen for a speaker match, and forty
for a warm inference, typed again. In the other directories the worker is built from: the
endpoint's pause gate waited one second for a verdict and fifteen for a question unanswered at
the input's end, which sat inside the thirty of the model call it waited on; and the separating
recognizer bounded its budget by four and twenty-five seconds and compared it with a typed
thirty.

Three of these could be met as faults. The worker holds a playback report until the audio write
it counts has ended, and gave that write three seconds; the carrier gave the report two. A
report held behind a stalled audio pipe therefore failed the carrier ("worker admission timeout")
before the worker's own deadline could say that the pipe had stalled. A drain was called "no
progress" after its fifteen seconds while a model call inside it was still within its own thirty:
a session that was ending cleanly failed and blamed the wrong thing. And a write past its
deadline was said in two ways: the pipe's own sentence, "native pipe write interrupted/expired",
named no limit and did not tell an expiry from an interruption; the main loop's sentences named
none either; and which was said depended on which thread saw the deadline first.

The carrier waited 180 seconds for its worker to report ready, counted from when it had started
the worker. A start that passed it said "worker startup timeout": not how long had been waited,
nor that the number is one a profile states. And 180 seconds is what each set of the package
declares to the host as the allowance for its start (`startup_ms`), so when the host's allowance
passed the carrier had said nothing yet. The carrier verifies its runtime's files before it
starts its worker, and that time was not counted at all.

None of these numbers was stated anywhere but in the code: every set ran on what was compiled
into its carrier, its worker and its libraries, and a set's runtime profile said nothing of the
limits it ran with.

**Now.** `plugin/native/limits.go` is the one table. It has 27 members, stated in the "limits"
member of the runtime profile (`voice-runtime.json`) in whole milliseconds, or by the defaults
below. Where a wait was typed before, its default is the number that was typed, except the
host's three storage times, `ready_ms`, `worker_exit_ms` and `endpoint_retire_ms`.

| Member                 | Default | Who waits by it, and for what                                 |
|------------------------|---------|---------------------------------------------------------------|
| `host_read_ms`         | 5 s     | carrier: one read the host answers                            |
| `host_write_ms`        | 12 s    | carrier: one durable write or publish the host answers        |
| `storage_wait_ms`      | 12 s    | carrier: the host's storage in all, an opening or a read      |
| `drain_idle_ms`        | 15 s    | worker: a session ending, nothing moving or in flight         |
| `reply_settings_ms`    | 150 ms  | worker: a reply's first segment, for the settings in force    |
| `abort_ms`             | 5 s     | worker: an aborted session's core, from the first abort       |
| `capture_close_ms`     | 45 s    | worker: an enrollment capture's close, preparation included   |
| `session_open_ms`      | 60 s    | worker: a session's open, a speaking model's load included    |
| `opening_notice_ms`    | 1.5 s   | worker: an opening, before it says what it waits for          |
| `control_ms`           | 2 s     | carrier: the worker's answer to a control without storage     |
| `audio_write_ms`       | 3 s     | worker: one write of audio to the host's pipe                 |
| `ready_ms`             | 175 s   | carrier: its worker's readiness, from the carrier's start     |
| `control_write_ms`     | 3 s     | worker: one write of a line to the carrier                    |
| `retire_ms`            | 5 s     | worker: its whole end, one deadline for both of its phases    |
| `capture_tail_ms`      | 2 s     | worker: an enrollment capture's last frames                   |
| `worker_exit_ms`       | 5.5 s   | carrier: its worker's exit, its input closed, before a kill   |
| `worker_reap_ms`       | 5 s     | carrier: a killed worker's exit                               |
| `lane_flush_ms`        | 2 s     | carrier: the host's lane taking the worker's last events      |
| `model_call_ms`        | 30 s    | engine: one model call, by the session's watchdog             |
| `input_tail_ms`        | 3 s     | engine: a conversation's last frames                          |
| `output_take_ms`       | 15 s    | engine: synthesized audio waiting to be taken by the worker   |
| `speaker_match_ms`     | 15 s    | worker: a final's wait for its speaker                        |
| `warm_probe_ms`        | 40 s    | engine and carrier: the warm inference of a worker's start    |
| `endpoint_decision_ms` | 1 s     | engine: the endpoint's verdict where a pause would end a turn |
| `endpoint_retire_ms`   | 30.5 s  | engine: an endpoint question unanswered at the input's end    |
| `separation_min_ms`    | 4 s     | engine: the least one separation of talkers is given          |
| `separation_max_ms`    | 25 s    | engine: the most one separation of talkers is given           |

Between its two bounds a separation has five times the separated audio's length; that ratio is
typed, and is not a time.

The rest is computed from these, with a margin of 500 ms (`limitMargin`), so that an inner waiter
always answers before the one outside it:

| Computed                    | Rule                                            | At the defaults |
|-----------------------------|-------------------------------------------------|-----------------|
| `exchange_read_ms`          | `host_read_ms` and the margin                   | 5.5 s           |
| `exchange_write_ms`         | `host_write_ms` and the margin                  | 12.5 s          |
| `opening_ms`                | `storage_wait_ms` and the margin                | 12.5 s          |
| `whole_read_ms`             | `storage_wait_ms` and the margin                | 12.5 s          |
| `whole_publication_ms`      | three write exchanges and one whole read        | 50 s            |
| a control that uses storage | two whole reads, one publication, `control_ms`  | 77 s            |
| a playback report           | `audio_write_ms` and `control_ms`               | 5 s             |
| the carrier's own storage   | twice `host_read_ms` and twice `host_write_ms`  | 34 s            |

The first five are the worker's: its wait for the carrier's answer to one page read, to one stage
or publish, for a session's settings, for every page of one file and its readback, and for the
stages, the publish and the read that verifies a publication. The next two are the carrier's
waits for its worker (`storageOperation`, `playbackReport`); a speaker operation or a recording
is given the first, where 45 s was typed, and every other control `control_ms`. The last is what
the carrier gives each of its own operations on the private files, none of which goes through
the worker: the recordings' list, a recording's deletion, the pruning of abandoned stages, and
the correction list's reads and changes (`ownStorage`). It was ten seconds typed at each, less
than the table gives one durable write.

What nests in what, and who holds it:

| Outer                       | Inner                                        | Held by           |
|-----------------------------|----------------------------------------------|-------------------|
| `host_write_ms`             | `host_read_ms`                               | carrier, refused  |
| `storage_wait_ms`           | `host_write_ms`                              | carrier, refused  |
| each of the worker's five   | the host's time for the same, and the margin | computed          |
| the host's 90 s for a call  | a storage control and 2 s for its answer     | carrier, refused  |
| the host's 90 s for a call  | a playback report and 2 s for its answer     | carrier, refused  |
| `worker_exit_ms`            | `retire_ms` and the margin                   | carrier, refused  |
| `output_take_ms`            | `audio_write_ms` and the margin              | carrier, refused  |
| `drain_idle_ms`             | `input_tail_ms` and the margin               | carrier, refused  |
| `ready_ms`                  | `warm_probe_ms` and the margin               | carrier, refused  |
| `endpoint_retire_ms`        | `model_call_ms` and the margin               | carrier, refused  |
| `model_call_ms`             | `separation_max_ms` and the margin           | carrier, refused  |
| `separation_max_ms`         | `separation_min_ms`                          | carrier, refused  |
| a set's `startup_ms`        | `ready_ms` and 2 s for the carrier's word    | assembly, refused |

The 90 s is the host's wait for one invocation of a plugin (`hostInvokeAllowance`). The reasons
are in `limits.go` beside each rule. `worker_exit_ms` was five seconds beside the worker's own
five, two equal numbers that began together, so the kill could come while the worker was still
inside its own time; it is now `retire_ms` and the margin, and a worker that is ending inside
its own time is never killed. The worker cannot take more audio from the session while a write
of it is in flight, so `output_take_ms` is outside `audio_write_ms`, and a pipe the host has
stopped taking is said by the pipe's own deadline. A drain's idle limit is outside the wait for a
conversation's last frames, so that frames that do not come are said as that and not as a
stalled drain. The warm inference is part of a worker's becoming ready. A question the endpoint
has not answered when the input ends is a model call in flight, so the wait for it is outside
`model_call_ms` and the call's own limit speaks first; it was fifteen seconds, inside the call
it waited on. A separation runs inside one model call and is given up at its budget, which fails
nothing, where the watchdog would fail the session; so `model_call_ms` is outside
`separation_max_ms`. With the ranges below, `model_call_ms` can therefore be stated from 750 ms
to 119.5 s.

A drain's idle limit is not held over the limits of what a drain can wait on by arithmetic: it
does not run while such work is in flight. Three things hold that clock, each with a limit of
its own that is then the one to speak: an operation on the host's storage, a model call
(`model_call_ms`, by the session's watchdog) and the worker's own write of audio to the host
(`audio_write_ms`, by the pipe's deadline). Audio waiting in the session's queue
(`output_take_ms`) waits only behind such a write, because the worker is that queue's one
consumer, and is held by it. It is one rule from one look (`DrainHold` in
`runtime/native/session/drain_hold.h`), and the worker looks on every pass of its loop: work that
has ended since the last pass renews the deadline on that pass, so the idle limit runs from the
drain's last work and not from a deadline that work ended inside. An abort waits for none of it.
The core's status says whether its watchdog holds a model call and how many it has given back.

Some members nest in nothing, and the table's comments say why. `control_write_ms`: the carrier's
one reader takes the worker's lines as they come. `capture_tail_ms`: the host sends those frames.
`worker_reap_ms` and `lane_flush_ms`: the operating system and the host answer them.
`speaker_match_ms` is the outer of nothing: the match still running is not ended by it, and one
that comes later is not used. `endpoint_decision_ms` is the outer of nothing too: the question
still running is not ended by it. A model call is not inside `session_open_ms`: an open loads a
model and the watchdog does not watch it.

A table that is out of range, or breaks a rule marked "refused", is refused, and the carrier does
not start on it. The range is 250 ms to 120 s. `reply_settings_ms` and `endpoint_decision_ms`
have 10 ms to 2 s, being time a listener waits, and `ready_ms` one second to one hour, being a
model's load. The profile around the member is read loosely, and the member itself strictly: a
name that is not one of the table's, in any letter case, and a null value are refused at the
carrier's start, so that no limit is misspelt and its default taken in silence.

The worker is handed 24 members at its start, in `AII_VOICE_LIMITS`
(`runtime/native/session/worker_limits.h`): the five computed ones, and the nineteen stated ones
marked "worker" or "engine" above. It reads them strictly (every member, each once, nothing
else), checks that they are in range and that its five nest, and does no arithmetic of its own
on them; a table that does not hold stops it before it loads a model. A value the carrier
inherited in its environment is never passed on. A carrier and a worker of different builds
refuse each other: a worker handed a table with other members says "malformed worker limits".
`control_ms`, `ready_ms` and the three waits of the carrier's own end are the carrier's alone.

The engine's nine are the worker's to hand on. The session library has two new entries for it,
`aii_voice_open_bounded` and `aii_voice_models_warm_within` (`c_api.h`, `native_runtime.def`).
The worker states seven of the nine to each session as it opens it (`model_call_ms`,
`input_tail_ms`, `output_take_ms`, the endpoint's two and the separation's two), and
`warm_probe_ms` to the model owner as it warms. It keeps `speaker_match_ms` itself
(`attribution.h`). The session passes the endpoint's two to its pause gate
(`configure_waits` in `runtime/native_endpoint/pause_gate.h`) and the separation's two to its
recognizer (`bound_separation` in `runtime/native_multitalker/separating_recognizer.h`). The
entries that state none, the session's header, the gate's and the recognizer's keep numbers of
their own for a caller that is a probe or a test; a worker never runs by them. `warm_probe_ms` is
one member for the library and the carrier, so the two cannot differ: the library fails a start
whose warm inference took longer, and the carrier refuses a report of one.

`ready_ms` is counted from the carrier's own start, the instant its start-up records already
count from, and not from the worker's: the check of the runtime's files and the worker's spawn
are inside it, as they are inside the allowance the host gives a start. When it passes, the
carrier writes on its standard error "the worker did not report ready within N ms of this
carrier's start, the time the limits table gives it (ready_ms); the worker was started M ms into
it", and a start-up phase line, and only then ends its worker and itself. If the time is already
spent when the worker has been started, the sentence is said at once. The default is five seconds
inside the 180 s the sets declare. The carrier is not told what its set declares, so the
package's assembly holds the two together: `startup_covers_readiness()` in
`scripts/runtime_limits.py` refuses a table whose `ready_ms` and two seconds for the carrier's
word together pass a set's `startup_ms`, and the assembly script applies it to every set where
it has both the profile and the checked declaration, naming the set.

What is said at each limit names the member and its number:

| Limit                                      | Said by  | As                                |
|--------------------------------------------|----------|-----------------------------------|
| `ready_ms`                                 | carrier  | at once, and in its end's reason  |
| `worker_exit_ms`, `worker_reap_ms`         | carrier  | at once, and in its end's reason  |
| `lane_flush_ms`                            | carrier  | in its end's reason               |
| the carrier's own storage                  | carrier  | in the operation's answer         |
| `warm_probe_ms`                            | library  | a failed start, with both numbers |
| `warm_probe_ms`                            | carrier  | a refused report of readiness     |
| `retire_ms`, `abort_ms`                    | worker   | a failure line, then status 72    |
| `capture_tail_ms`                          | worker   | the session's failure             |
| `drain_idle_ms`                            | worker   | a failure                         |
| `audio_write_ms`, `control_write_ms`       | the pipe | "native pipe write expired: ..."  |
| `model_call_ms`, `input_tail_ms`           | core     | the session's failure             |
| `output_take_ms`, `endpoint_retire_ms`     | core     | the session's failure             |
| `speaker_match_ms`, `endpoint_decision_ms` | worker   | a line of its log; nothing fails  |

The carrier's sentence for a forced end ("worker required forced cleanup: it did not exit in N
ms after its input was closed, the time the limits table gives it (worker_exit_ms)") is written
before the kill. The worker's line for `retire_ms` says what had not ended: a session, a
session's open, an enrollment, a capture's close, a recording's publication, audio not yet
written, or its input and output threads. The drain's sentence is "native drain made no progress
for N ms, the time the limits table gives it (drain_idle_ms)". The core's sentences are the
wait's own words, then "N ms, the time the limits table gives it (member)". A verdict of the
endpoint that is late leaves its turn to end by silence alone, and the worker's log says so in
an `AII_VOICE_ENDPOINT` line; a speaker match that came late is an `AII_VOICE_SPEAKER` line. A
separation given up at its budget leaves its turn the words that were heard unseparated, and the
recognizer's own report counts it.

The pipe is told which member its limit is, and has two sentences. A write that has had its time
says "native pipe write expired: N ms, the time the limits table gives it (MEMBER)", whichever
thread sees it first, the one that writes or the main loop. A write stopped by its owner inside
its time says "native pipe write interrupted". The main loop says the pipe's sentence at its
four places and has none of its own.

A built set states its limits. `scripts/runtime_limits.py` is the scripts' one statement of the
table (names in the carrier's order, defaults, ranges, the rules that make them nest, the host's
allowance), held to `limits.go` by a test that reads that file.
`scripts/rebuild_native_checkpoint.py` and `scripts/stage_nemotron_macos.py` write the table into
the profile they make: the table in `--limits FILE`, or the parent's, with any member the parent
does not state at its default. `scripts/package_native_runtime.py` `verify()` refuses a table
that cannot hold. `scripts/stage_qualified_runtime.py` and the assembly script refuse a profile
that does not state every limit, and the stage's result records them. Nothing builds, stages or
assembles a set that does not state its limits. The tests' stand-in for the carrier
(`scripts/prove_native_worker_transport.py`) states the carrier's default table to the worker
unless a test passes its own, and never passes on one it inherited; `tests/native_limits.py`
holds the values the carrier computes from its defaults.

No operational wait is typed in the carrier, the worker, the session library or the other
directories the worker and its libraries are built from, and tests hold the lists of every
duration that is still written there, each with the reason it is not a wait. One more typed
without being listed fails them: it becomes a member of the table, or it is listed with what it
is.

- In the carrier (`TestNoDurationIsTypedInThisCarrierButThoseListedWithAReason`, six files): the
  window in which a confirmation of an act is fresh, ten minutes back and one ahead, which is
  what is accepted and not a wait; how often the one owner of the controls' deadlines looks at
  them; the age, two minutes, at which a staged upload is taken for abandoned, which is over
  every publication the table can give; and how often the check of the runtime's files says in
  the log how far it has got.
- In the session directory
  (`test_no_duration_is_typed_in_the_session_directory_but_those_listed_with_a_reason`, every
  source but the tests, the probes included): the table's own ranges; the numbers `session.h`
  keeps for a caller that states none, and the ranges of what may be stated; a person's own
  settings, the pause that ends a turn and how long a recording may be by default; a time of day
  that is carried, and a reading's first value; in the worker, how long input must have been
  held before the worker says so in its log, a future asked whether it is ready without waiting,
  and how often its loops look again; and the probes, programs a developer runs by hand against
  real models, which are in no engine and no worker.
- In the other directories, each with every directory under it (the test is
  `test_no_duration_is_typed_in_what_else_the_worker_is_built_from_but_those_listed_with_a_reason`
  in `tests/test_runtime_limits.py`): the recognizer's, the voice detector's, the endpoint's, the
  echo canceller's, the separating recognizer's, the speaker models', the speech model's resident
  runtime and the platform's. What remains there: the numbers the endpoint's gate and the
  separating recognizer keep for a caller that states none; what the endpoint's work is asked to
  take, 250 ms, which is told to a scheduler and written in a trace and which nothing waits by;
  how long speaker evidence that is never stored is kept before it is dropped, ten minutes, an
  age; the length and the step of a frame of the speaker model's features, the model's own
  arithmetic; and the probes. A directory one of the native recipes comes to draw on is read
  there too.
- The two sources of the speech model's resident runtime that the session library compiles, and
  the one header they include, type no duration and wait on nothing
  (`test_the_resident_speech_sources_wait_on_nothing`): what a synthesis call may take is the
  session's `model_call_ms`, around the call.

The header's strict reader was first written with a loop counter that was set and never read.
GCC takes that; Apple clang refuses it under the tree's own warning flags, in a header every
target includes, and the tree did not compile on macOS. The counter is gone.

`docs/NATIVE_WORKER_WIRE.md`, `plugin/NATIVE_BUILD.md` and the documents that named a typed
number say what the table says.

**Shown.** On Linux, on the tree of the last source change: the fixture build 47 of 47 and a
development build 57 of 57 with GCC, and the fixture build 47 of 47 with clang 18 under the
tree's warning flags; `go vet` for linux, windows and darwin; the carrier's package with the
fixture worker, 118 tests and 86 subtests, none skipped; the limits and wire document tests, 204
cases; the 16 modules that drive the fixture worker, 160; the model-free list, 800; and the
build-contract and closeout modules, 70.

In the carrier, `limits_test.go` holds the arithmetic: `TestEveryOuterLimitCoversWhatIsInsideIt`,
`TestAWriteHasItsOwnLimit`, `TestALimitsTableThatCannotHoldIsRefused`,
`TestTheWaitsOfAWorkersEndAreTheTables`,
`TestTheWorkersDefaultsAreWhatTheCarriersDefaultsComputeTo`, `TestTheEnginesWaitsAreTheTables`,
`TestTheEndpointsWaitsAndASeparationsBudgetAreTheTables`,
`TestAStorageControlEndsInsideTheHostsAllowance`,
`TestTheDefaultWaitForReadinessEndsInsideTheStartASetDeclares`,
`TestALimitThisCarrierDoesNotKnowIsRefused` and
`TestAProfileWhoseLimitsAreNotUnderstoodDoesNotStart`. `limits_behaviour_test.go` holds the
carrier to using the table, through its own paths: `TestAQueryOfTheSpeakerFilesWaitsByItsKind`,
`TestAControlIsGivenWhatItsStorageMayTake`, `TestTheWorkerIsHandedTheProfilesLimits` (its
profile's table exactly once and never an inherited one) and
`TestAWorkerThatDoesNotReportReadyIsWaitedForTheTablesTime`. `worker_end_test.go` holds the
carrier's end and its own storage: `TestThisCarrierWaitsForItsWorkersExitByTheTable`,
`TestThisCarrierSaysWhyItKillsItsWorkerBeforeItDoes`, which reads the log at the kill, and
`TestThisCarriersOwnStorageOperationsAreGivenTheTablesTime`. `readiness_test.go` has
`TestAWarmInferenceIsGivenTheTablesTime`, and `ready_wait_unix_test.go` starts a packaged carrier:
`TestAPackagedCarrierSaysItsWorkerDidNotReportReadyInTheProfilesTime` and
`TestTheWaitForReadinessIsCountedFromTheCarriersOwnStart`.

The clock was measured with a packaged carrier and a 2 GiB runtime file whose check took 921 ms:
with `ready_ms` 1900 the carrier gave up 1.9 s from its own start; with `ready_ms` 1000 at 1.0 s,
the sentence said at once. What verification costs on real sets was read from an earlier run's
carrier logs, and no engine was run for it: 25 to 37 ms for the two small sets (32 files, 151 MB)
and 386 to 409 ms for the full one (46 files, 2.5 GB), the file cache's state unknown.

The drain's rule is held by its unit test with an injected clock (`drain_hold_test.cpp`,
`native_drain_is_held_by_work_in_flight`): storage that ends 4 s into a 15 s deadline gives a
deadline of 4 s plus 15 s, and no second renewal at expiry; the same for a model call; a write of
audio holds and renews nothing. Through the fixture worker, each within 0.3 s early and 1 s
late: a model call that ends inside a drain, one that passes `model_call_ms` (the model's
sentence, not "no progress"), a write of audio that ends inside a drain, and a drain with nothing
in flight. The tests are in `tests/test_native_drain_progress.py`
(`test_a_model_call_longer_than_the_idle_limit_does_not_fail_a_drain`,
`test_a_model_call_past_its_own_limit_inside_a_drain_is_said_as_that`,
`test_a_drain_with_nothing_in_flight_fails_at_the_tables_idle_limit`,
`test_a_drains_idle_limit_runs_from_when_a_model_call_ended_in_it`) and in
`tests/test_native_output_only.py`
(`test_a_drain_behind_audio_the_host_does_not_take_is_said_by_the_audios_own_limit`,
`test_a_drains_idle_limit_runs_from_when_its_last_write_of_audio_ended`).

In the worker, `worker_limits_test.cpp` (`native_worker_limits_are_read_strictly_and_nest`).
Where the fixture worker can reach a limit, a test drives it with the limit set short:
`test_a_model_call_is_given_the_tables_time` (`tests/test_native_model_progress.py`); in
`tests/test_native_fault_scope.py`
`test_a_conversations_last_frames_are_waited_for_the_tables_time`,
`test_a_final_waits_for_its_speaker_the_tables_time`,
`test_a_turn_waits_for_the_endpoints_verdict_the_tables_time`,
`test_an_endpoint_question_unanswered_at_the_inputs_end_is_waited_for_the_tables_time` and
`test_a_captures_last_frames_are_waited_for_the_tables_time`; and in
`tests/test_native_output_only.py`
`test_audio_that_is_not_taken_fails_the_session_at_the_tables_time`,
`test_a_worker_that_cannot_end_gives_up_at_the_tables_time_and_says_what_it_waited_for`,
`test_a_stalled_audio_write_retires_the_worker_at_the_tables_time` and
`test_a_stalled_control_write_retires_the_worker_at_the_tables_time`. The fixture worker is built
with the speaker model's seam and takes a policy as a carrier gives it, so an enrollment
capture's last frames are waited for through it. The warm inference's limit the fixture cannot
reach, and is driven in the library's own test (`c_api_test.c`) and in the carrier's. The pipe's
one sentence, whichever thread sees the deadline, is held by
`test_a_write_that_has_had_its_time_is_said_by_its_pipe_whichever_thread_sees_it`. For the
scripts, `tests/test_runtime_limits.py` reads the carrier's source for the member names and
their order, the defaults, the ranges, the host's allowance and the margin, holds rebuilding,
staging and assembly to stating every member, and holds the worker's statement of the engine's
waits by its source (`test_the_worker_states_the_engines_waits_from_the_table`).

Two timings from earlier rounds still stand. With `audio_write_ms` 1200 in the table a stalled
audio write retires the worker in 1.27 s, and at the default not before 2.9 s. With the real
carrier and the fixture worker, a report held behind a stalled audio write ends on the worker's
own failure, 3.009 s after the stall
(`TestAReportHeldBehindAStalledAudioWriteWaitsForTheWorkersOwnDeadline`).

Faults were planted in copies of the tree, each check first run with none. In the last round
there were 46, and all were caught, by 87 checks. In the round before it there were forty, and
39 were caught, by 84 checks; the fortieth, the carrier's sentence for a forced end coming before
the kill, had no check then and has its test now. In the rounds before those, thirteen faults in
the carrier's use of the table, ten where a built set states its limits and eight for the wait
for readiness were each caught by a test; with the strict reading disabled, a misspelt limit and
a null one decoded without error and the default was taken.

**Not shown.** The defaults are chosen, not measured, and the table's comment says so. One set's
worker reported ready 11.1 s after its start on the processor it ran on and 194.7 s after it
under a processor emulator; that start is past the default and past the allowance, and the
table's comment records it. How the host counts `startup_ms`, and what it does when it passes,
was not read. That every set declares 180 s was read in an earlier assembled package, not in this
tree.

Nothing of the last round was built with MSVC or Apple clang, or run on Windows or macOS; its
changes to the tests for Windows were written by reading. Storage inside a drain is shown by the
unit test only: the tests' stand-in for the carrier has no storage.
`runtime/native_audio/linux/pulse_host.c` and the Android reference still type a wait; neither
is in a desktop set's worker.

## The wire between the carrier and the worker, written down

**Was wrong.** The private wire between the Go carrier and the C++ worker (the worker's start and
its environment, the control lines both ways, the audio pipes, the diagnostic lines) was
specified nowhere. It existed as implementations that were never compared: the carrier, the
worker, and the Python stand-in the fixture's tests drive the worker with.

**Now.** `docs/NATIVE_WORKER_WIRE.md` states it: process start (command, environment, the limits
table, readiness, what ends the worker and its exit statuses); the control channel in each
direction, line by line and member by member; ordering and matching; the audio pipes; the
diagnostic lines; and section 7, where the implementations differ, each difference with the file
and function to look in. Its first table is computed: for each kind of line, the members one side
writes that the other never reads, and the members one side reads that the other never writes.
The document says of itself that nothing in it is a promise to a host or to a plugin author: the
public contract is the plugin kit's.

Writing it down found things that this release corrects and that have their own sections here: a
storage request for the correction list accepted from a worker, the interpreter engines'
readiness still taken, and two typed waits that did not nest.

**Shown.** `tests/test_native_worker_wire_document.py`. Every operation, event type, line member,
limits member, reason code, diagnostic name, exit status and storage word the source holds must
be named in the document (`test_every_wire_name_in_the_source_is_in_the_document`). Each rule
that takes names from the source must find a known few, so a rule that stops matching fails and
does not pass empty. The table of section 7 is recomputed from the source and must equal what the
document states (`test_the_table_of_differences_says_what_the_source_does`). And the carrier's,
the worker's and the tests' limits tables must name the same members
(`test_the_three_copies_of_the_limits_table_name_the_same_members`). With the worker reading the
settings reason under another name, planted in a copy of the tree, the test fails, as the table
then differs from the one the document states.

**Not shown.** Event members and status members are documented and not machine-checked. The
worker's side of each difference in section 7 was read, not run. The differences that remain in
section 7 are stated, not corrected.

## Corrections and labels hold text that can be read as written

**Was wrong.** A correction is confirmed by a person reading it, and what it writes is recorded as
a speaker's words. Either side of a rule could hold characters that cannot be read where they
stand: zero-width characters, direction marks, embeddings, overrides and isolates, tag
characters, line and paragraph separators. The worker's reader of UTF-8 also took forms that are
not the shortest, so a looser reader downstream could find a line feed in bytes that were never
judged as one. A speaker's label had the same gap: it is a name shown to the person who confirms
it.

A side of a correction was bounded at 64 bytes while the operations' input schemas, which the
host holds a call to before anyone is asked to confirm, count 64 characters. A name of 22
Chinese, Japanese or Korean characters passed the schema and the confirmation and was then
refused.

**Now.** Each side of a correction (`runtime/native/session/corrections.h`;
`plugin/native/vocabulary.go`; the vocabulary schemas) is well-formed UTF-8 in its shortest form,
at most 64 characters (code points) and 256 bytes, with no character of Unicode's general
categories Cc, Zl, Zp (ends the line) or Cf (format). The worker lists the code points (Unicode
16.0.0; 15.0.0 and 17.0.0 list the same); the carrier asks its toolchain's tables; the schemas
say it as a pattern. The sentences say characters where they said bytes.

One exception is the same for what a correction writes (`meant`) and for a speaker's label. The
zero width non-joiner and joiner (U+200C, U+200D) are ordinary spelling in Persian and Indic
names and in joined emoji, and are taken inside a word: between two characters that are neither a
space (general category Zs) nor a joiner. First, last, alone, beside a space or beside another
joiner a joiner is refused ("meant holds a joiner outside a word; a joiner is taken only inside a
word"). `heard` takes neither joiner: nothing here establishes that the recognizer writes them.
The carrier shares one function between the label and the correction (`joinerInAWord`,
`enrollment.go`), and the `meant` pattern of `vocabulary-correct.input.json` is the label's
pattern byte for byte.

A label (`readable_label` in `runtime/native_uid`, called from `enrollment.cpp` and
`speaker_registry.cpp`; `plugin/native/enrollment.go`; the two speaker schemas) follows the same
rule. A label already stored is not judged again: it is read as stored.

The recognizer refuses a whole list of terms that holds one longer than it takes (64 bytes), and
a refused list fails the session. With the bound counted in characters a `meant` can be longer
than that, so a `meant` longer than 64 bytes is not offered to the recognizer as a term to
prefer, and its rule still rewrites what was heard. The session's readback says how many were not
offered (`too_long_to_prefer`, present only when it is not zero).
`docs/RECOGNIZER_CORRECTIONS.md` says the rule, the bound and the term.

**Shown.** `spec/correction_vectors.json` (33 valid rules, 67 invalid) and
`spec/uid_label_vectors.json` hold the worker, the carrier and the schemas to one answer, with
the joiners and the spaces as tables a test holds equal between the two files. A test on each
side walks every code point, so when Unicode adds to these categories the side that moved fails
before a list one side wrote is refused by the other. On Linux `aii_corrections_test` (with a
rule whose `meant` holds a non-joiner rewriting a transcript twice with those exact bytes),
`aii_uid_label_contract_test` and `aii_speaker_registry_test` built and passed, with the
carrier's vocabulary, label, enrollment and schema tests, and `tests/test_native_corrections.py`
against the fixture worker (among its tests
`test_a_meant_longer_than_a_term_costs_no_session_and_is_said` and
`test_what_was_meant_is_written_with_its_joiners_and_what_was_heard_takes_none`).

Faults were planted one at a time in copies of the tree and each was caught: characters counted
as bytes, a bound missing, a length refusal naming the wrong side, the recognizer handed an
over-long term, the readback counting the wrong thing; `meant` refusing joiners, a joiner taken
anywhere, first or last, beside a space or beside a joiner; `heard` taking joiners; a space of
category Zs not counted a space; the sentence differing between the worker and the carrier. One
fault passed at first (a readback with one long term cannot tell two counts apart) and the test
was changed to three rules with two long ones.

**Not shown.** How the host treats the added readback member was not checked; no schema or
decoder in this tree reads it. That the recognizer passes over a term its pieces cannot spell was
read (`Recognizer::prefer`) and not run: it needs models.

## What the carrier refuses

**Was wrong.** Three things were accepted that no released engine sends.

The carrier's own calls on the correction list use the type of the worker's storage request, and
so that type's check accepted the list's resource word from anyone. A worker that wrote a
`snapshot_request` naming "corrections" was served as the carrier is, for a read, a stage and a
publish: it could have read the list and replaced it with no confirmation asked. No worker asks
for it; the storage bridge has no such store.

A runtime profile with no native member named an interpreter, a bootstrap script and a site
directory, and the packaged carrier started it as `<python> -I -S -B <bootstrap>`. No released
engine is one: every released desktop set is the native worker.

The carrier's check of a worker's readiness took three models under the accelerator "metal",
"cuda" or "directml" with no backend named. Those are the reports of the interpreter engines,
which no released engine writes.

**Now.** The correction list is the carrier's: it changes only by a confirmed operation
(`vocabulary.correct`, `vocabulary.forget`), and a session is handed its copy in the settings
reply. `workers()` in `plugin/native/snapshot.go` says a request names storage that is the
worker's, and a worker's line that names anything else faults the lane ("private snapshot request
for a resource that is not the worker's"), as a malformed request does.

`verifyRuntime` in `plugin/native/runtime.go` refuses a profile with no native member, after the
manifest's binding is checked and before one file of its inventory is read. One that names any of
`python`, `bootstrap` or `site` is told what it is ("this runtime profile describes a Python
engine ..."); one that names nothing is told "runtime profile names no native worker". The
interpreter launch is gone and the three interpreter backend names are no longer accepted. A
stand-in worker named on an unbound development carrier's command line is untouched.

`readinessReport` in `plugin/native/main.go` takes a report when its backend is one of
`native-common-cpu`, `native-common-vulkan` and `native-common-metal`, its accelerator is that
backend's (`cpu`, `cpu_vulkan`, `cpu_metal`), it loaded four models or five, and its measured
probe is within the table's `warm_probe_ms` (40 s by default, the number that was typed). A
worker handed a recognizer from outside still reports "external_recognizer" and is still
refused: that word names no measured placement.

**Shown.** `TestReaderDrainsTheWorkersTailAfterAFault` has rows for the list read, staged and
published by a worker, each well formed, and `TestTheCorrectionListIsNotAWorkersToReadOrReplace`
holds that the carrier's own query is still well formed and that every resource a worker may name
is still served. `runtime_python_profile_test.go`: `TestAPythonProfilePackageIsRefusedAtStart`
(six profiles: whole; interpreter, bootstrap or site alone; under backend "native"; with its
files deleted, so it is refused for what it is and not for a missing file),
`TestAProfileThatNamesNoWorkerIsRefused`, `TestTheNativeProfileIsStillAcceptedAndStarted` and
`TestADevelopmentCarrierStillStartsTheWorkerItIsHanded`. `readiness_test.go`: the three
interpreter reports, three models under a native backend, six models, and "external_recognizer"
under two backends are refused; the native reports that were accepted still are. For the first
two refusals, faults planted in copies of the tree (the check removed, the old acceptance
restored, the refusal reaching what must still be served) each fail one of these tests.

**Not shown.** `TestPackagedCommandUnderWindowsVolumeMount` compiles under vet for Windows and
was not run. That every released set reports one of the three native pairs follows from the
worker's source (`native_c_api.cpp`), which was read and not run against a real worker.

## Nothing in the tree builds an interpreter engine

**Was wrong.** No released desktop set has been an interpreter engine since 0.1.0-beta.7. The
tree still built one. `scripts/package_native_runtime.py` `build()` froze a CPython interpreter,
its site-packages and the Python engine into a runtime under a profile that named the
interpreter, a bootstrap script and a site directory; `plugin/runtime_bootstrap.py` was that
packaged interpreter's entry. The scripts that rebuild, stage, rebind, restore, package and
assemble a runtime took a parent with such a profile like any other. With the carrier's refusal
of such a profile at its start (the section before this one) and nothing else, an interpreter
engine would be refused only after everything had been built.

**Now.** `build()` and `dependency_closure()` are removed from
`scripts/package_native_runtime.py`, and `plugin/runtime_bootstrap.py` is removed. Called with
neither `--verify` nor `--bind-carrier` the tool says it packs nothing, and writes nothing. The
tests of the removed packer are removed with it.

`refuse_interpreter_profile()` is one sentence in one place: a parsed profile that states
`python`, `bootstrap` or `site` is refused, a native profile passes. It is called by everything
that would make something of a runtime, each where it reads the profile it would build on:
`bind_carrier()` before any toolchain is run, and the scripts that rebuild a checkpoint, stage
the macOS set, rebind a Windows set, restore a staged checkpoint, stage a release archive,
package a checkpoint, and assemble the package.

`verify()` is not changed and refuses nothing more than it did: whether the bytes of a runtime
that exists are what its profile binds may be asked of an old runtime too.

The Python engine's own source stays in the tree (`runtime/plugin_engine` and what it imports),
kept as a double of the native worker for the tests, and `README.md` says so.

**Shown.** `tests/test_python_engine_is_not_built.py`, 55 cases: the packer and the bootstrap are
gone; an intact runtime that describes an interpreter still verifies and one with a byte changed
differs; each of the eight places refuses each of four shapes (`python`, `bootstrap`, `site`, all
three) and writes nothing; beside each, a native profile goes on to the next thing that script
needs. In a copy of the tree the refusal was taken out of one place at a time: each time exactly
that place's four cases failed and the other 51 passed. Put back inside `verify()` in the copy,
the four cases of the runtime that still verifies failed and the other 51 passed. `verify()` was
compared on 11 profile shapes in 4 states against the tree before the change with the same limits
table on both sides: 44 answers, none differs.

**Not shown.** No writer was run to completion on a real runtime: each is driven to its refusal
and, as a control, to the first input its test does not supply.

## The test gates

**Was wrong.** More than thirty test modules ran in neither the CI workflow nor the closeout
gate. Both lists are typed by hand, so a module written and never listed proved nothing. No gate
ran `go test` on the carrier at all. The closeout's floor counted the report's total, which
includes subtests where they are reported (609 for 605 cases in recorded runs), and its time
limit was a typed 300 s that ended a slow run in a traceback.

Most checks in the release gates and proofs under `scripts/` are `assert` statements. Python
started with `-O`, or with `PYTHONOPTIMIZE` set, compiles them away in every module of the
process: a gate ended with exit code 0 having checked nothing.

**Now.** Every `tests/test_*.py` is in the workflow (`.github/workflows/source-contracts.yml`),
in the closeout's list (`TESTS` in `scripts/validate_source_closeout.py`), or named in
`OUTSIDE_THE_GATES` there with the reason and where it does run. Three are named outside: one
needs a Swift toolchain, one needs torch, one needs aiohttp. The workflow builds the model-free
fixture worker and the enrollment codec probe for the modules that need them.
`tests/test_carrier_with_fixture_worker.py` runs the carrier's Go tests that need the fixture
worker inside the closeout, and reads which they are from the source.

The closeout's floor is a number of test cases (`MINIMUM_CASES`, 727: what its 51 modules collect
on Linux) and its time limit an argument (`--time-limit`); at the limit it writes a failed result
that says so. Every result states its seconds, its cases, and how many of them exercise the
Python double and how many the engine that ships: `PYTHON_DOUBLE` names the 31 modules that are
the double's.

The time limit's default is 540 s. The gate took 242 s on macOS at the last round of the limits,
with 727 cases, and the default is twice the run on the system the gate is run on, rounded up to
a whole minute.

`require_assertions()` in `scripts/_assertions.py` ends the process with one sentence when
asserts are removed. Every file under `scripts/` that holds an assert, and every program there
that imports one that does, calls it as its first statement. The libraries call it too, because
programs kept outside this tree import them directly.
`runtime/native_uid_ecapa/generate_tables.py`, which is run by hand from its own directory,
carries the same check inline. No existing assert was rewritten and nothing a gate checks
changed. Scripts are run with the repository on the path (`python -m scripts.X`).

`tests/test_signed_windows_rebind.py` takes Go from the path. It named a toolchain by a typed
path and could not run where Go is installed elsewhere; it now passes whole on Linux.

The fixture's seam that holds a session's open reads its variable through the fixture's own
reader. The direct call is one MSVC refuses under the fixture target's warnings, so the fixture
worker did not build on Windows and no test that drives it could run there; an unsigned trial on
Windows found it.

The same trial found a fault in a proof's helper. `scripts/native_loaded_images.py` reads which
images the worker has loaded, and on Windows it found the worker as the process whose parent
number is the carrier's. Windows keeps a parent's number in a child after that parent has ended,
and gives the number out again, so a program started long before the carrier could carry the
carrier's number and be counted as its child: a proof whose engine was ready and sound failed by
chance of process numbers. The listing now asks for the parent's creation time and takes only
processes created no earlier (`tests/test_loaded_recognizer_images.py` holds the command). The
command itself has not run on Windows.

`requirements-test.txt` pinned scipy 1.15.3. On macOS 27 the loader refuses one library of that
wheel, so `scipy.signal`, `linalg`, `fft`, `sparse` and `io.wavfile` could not be imported there:
the pinned environment could not resample on the system the closeout gate is run on. No test of
the gate reaches `scipy.signal`, so the gate passed at the pins and nothing said so; and every
earlier run of the gate there had used an environment that held none of the pins. The pin is now
scipy 1.17.1, with a comment that says why.

**Shown.** `tests/test_every_test_module_has_a_gate.py`: every module is gated or named outside
and never both; nothing listed is missing; the workflow's list is read from its own commands; the
double's modules are named. Planted in copies of the tree, an unlisted module, a listed module
removed, a module both gated and outside, a module taken out of the workflow and a double not
named each fail it. Each module that was placed was first run by itself on Linux, and none failed
for a reason of its own.

`tests/test_release_gates_refuse_optimised_python.py` holds the rule by reading every file's
syntax tree, runs real gates under `-O`, `-OO` and `PYTHONOPTIMIZE=1` (refused, with the
sentence) and without (not refused), and requires itself to be in the workflow's list and the
closeout's. In a copy of the tree seven mutations of the guard (removed from three kinds of file,
moved below an import, the call removed, an assert added to an unguarded program and to an
unguarded library) each named the omission.

For the scipy pin: a new environment made from `requirements-test.txt` on macOS 27 (Python
3.12.13) holds all eight pins, imports `scipy.signal` and resamples, and the closeout gate
rehearsed in it on a copy of the tree passed (712 cases, 209 s). On Linux the environment made
from the file (Python 3.11.4) installs with numpy 2.2.6 unchanged and imports `scipy.signal`.

**Not shown.** A run of the whole closeout on the list as it is in this release, which collects
727 cases: the gate's last run, on macOS, was at the commit before the last round of the limits
(717 cases, 216 s; last section). The workflow was not run on its own runner. The edited
scripts were tried by `--help` or by import under the three optimised forms, not by running each
gate. Programs outside this tree that decide on `assert` themselves are not changed. The Windows
test environment is not made from `requirements-test.txt`, and whether scipy 1.17.1 resamples to
the same samples as 1.15.3 was not compared.

## Every file of the speech engine that a build replaces is in the tree, with a notice

**Was wrong.** The speaking libraries are built from audio.cpp at a pinned revision, and the
builds do not take that revision's source as it stands. The Windows build of the speaking library
compiles two files in place of the engine's own (`MIMI_OVERRIDE`, `ACOUSTIC_OVERRIDE`); before
this release they were in no repository.

Stating those two files was not the whole of it. Compared with the upstream repository's own
listing at that revision, file by file (4,524 files), the engine source the macOS library was
built from differs in nine files, the one the Windows library was built from in six, and the
engine source kept from the time of the Linux library in eleven. Five of those files, the same on
every system, make the transformer blocks of the Pocket speech model compute GELU by its tanh
approximation; one of the five is a file of the bundled ggml. None of the five was in the tree or
in any statement of what was changed.

The Windows notice, as it was first written for this release, gave as the upstream digest of
`mimi_decoder.cpp` the digest of a file that already held one of those changes. So it said
"seventeen lines added in one place" of a file that differs from upstream in three places.

Two documents said the macOS speaking library is built with `capacity-history.patch`.

**Now.** `runtime/native_pocket/engine_overrides/` holds the five files (`vec.h` of ggml,
`transformer_blocks.h` and `transformer_blocks.cpp`, `flow_lm.cpp`, `graph_common.h`), each with
a four-line head comment, and a NOTICE that gives for each the upstream path, the upstream file's
sha256, the built file's sha256 and what was changed, with ggml's licence whole. Together the
five, and two places of `mimi_decoder.cpp`, make the transformer blocks of the Pocket speech
model compute GELU by its tanh approximation, as that model's reference implementation does,
where the engine's transformer blocks compute it by erf. Blocks of the engine's other models keep
the engine's default. The notice says what was compared: the engine source the Windows libraries
were built from and the one the macOS libraries were built from, each against the upstream
revision file by file, 4,524 files, none missing and none added.

`runtime/native_pocket/windows_resident/overrides/` holds `acoustic_model.cpp` and
`mimi_decoder.cpp` as they were built, each with a four-line head comment, and its NOTICE is
corrected. `acoustic_model.cpp` differs from upstream in one line: a stream that reaches its
frame limit before a natural end is an error where upstream returns no step. `mimi_decoder.cpp`
differs in three places, and the notice states the file's real upstream digest: in two, a
streaming transformer block is configured through `graph_common::pocket_transformer_config`; in
the third, seventeen lines are added that mark the tensors the owner reads after the graph has
run as outputs of the graph. The notice also states the intermediate file the Windows build's
engine directory holds, the decoder without the seventeen lines: the engine's own library is
compiled from that directory, and the speaking library compiles the file in the tree. The macOS
build has both files in place of the engine's own and applies `metal-library-beside.patch` to
the bundled ggml's Metal backend in the source.

`runtime/native_pocket/engine_overrides_linux/` holds the three files in which the kept Linux
source differs from the other two systems' (`mimi_decoder.cpp`, `acoustic_model.cpp`,
`session.cpp`), with a NOTICE that says what is not known. How the Linux speaking library was
built is not recorded: no command, log or build directory of that build is kept. An engine source
of that time is kept. Every change these three files make, and every change of the five, was
found in the shipped library's own code. That the library was built from exactly these bytes is
not established. In that kept source the decoder also runs everything after its transformer
through a streaming tail runtime where the weights are on a Vulkan backend; the acoustic model
has the frame-limit error and one change more; and `session.cpp` chooses the prompt capacity for
a graph that is not planned on the host and times the stream loop's calls.

`capacity-history.patch` makes two changes, and the notices say which file holds which. Neither
is in the Windows or the macOS library. The kept Linux source holds the second (a prepared step
runtime is reused only when its prompt capacity equals the one asked for, in
`acoustic_model.cpp`) and not the first (the capacity chosen for a graph planned on the host, in
`session.cpp`). `docs/DEVELOPMENT.md`, the session library's README and `PROVENANCE.md` say the
same, the patch by its two changes. What the difference between the systems does to a reply
spoken after a longer one is in the first section.

Three more files of audio.cpp are adapted for reading models by handle inside a contained Windows
process. They are not kept as files: `scripts/stage_native_path_shim.py` derives them from the
three unmodified upstream files (`tests/fixtures/native_path_upstream`). The kept Linux source
has those three as well.

`scripts/prepare_engine_source.py` writes the engine source a build is given from an upstream
checkout, in three layouts, and writes nothing unless every upstream file a notice names is the
upstream's and every file it puts in is the one a notice states. It reads only the upstream
directory and this tree, downloads nothing and builds nothing.

- `windows`: the five files, and the decoder as that build's engine directory held it, which is
  the tree's `mimi_decoder.cpp` without its one added block. The Windows recipe compiles the two
  files of `windows_resident/overrides` itself, from where they are.
- `macos`: the five, and the two files of `windows_resident/overrides` in place of the engine's
  own. The recipe requires `metal-library-beside.patch` applied in that directory as well; that
  is one command, which the script prints and does not run.
- `linux`: the five, the three files of `engine_overrides_linux`, and the three files that
  `scripts/stage_native_path_shim.py` derives. It is the engine source kept from the time of the
  Linux library's build, which no record ties to that build.

**Shown.** The script's output from a pristine copy of the upstream revision (itself held to the
listing: 4,523 of 4,524 files identical, the other by its line ends) was compared with each
directory. The Windows layout equals the directory the shipped Windows library's build names,
4,524 of 4,524 files by sha256. The macOS layout, with the Metal patch applied, equals the
directory the macOS build left, 4,524 of 4,524 by content id. The Linux layout equals the kept
Linux source, 4,524 of 4,524 by sha256.

`tests/test_engine_override_notice.py`, 23 cases: each directory's files are the notice's, each
file without its head is the bytes stated, each holds the change its statement names, the two
notices name each other, and the Linux notice keeps what is not known
(`test_every_replaced_engine_file_is_stated_and_is_what_was_built`,
`test_the_linux_sources_three_files_are_stated_with_what_is_not_known`); 21 statements made
untrue in a copy are each refused, each for its own reason
(`test_a_statement_that_is_not_true_of_the_files_is_refused`,
`test_a_linux_statement_that_is_not_true_of_the_files_is_refused`).

`tests/test_prepare_engine_source.py`, 13 cases, on a small made-up upstream and on the tree's
own files: the three layouts
(`test_the_windows_layout_holds_the_replaced_files_and_the_decoder_without_its_block`,
`test_the_macos_layout_holds_both_replaced_files_whole`,
`test_the_linux_layout_is_the_five_the_three_kept_and_the_three_derived`), the decoder without
its block held to the digest the notice states, and nothing written where a digest does not hold
(`test_nothing_is_written_where_a_digest_does_not_hold`). Both modules are in the workflow.
`tests/test_windows_override_notice.py` still holds the Windows directory to its notice and to
the recipe.

**Not shown.** No speaking library was rebuilt from these sources: the libraries are the ones the
last published release carried. How the Linux library was built is not recorded, and that it was
built from the kept source is not established. What the tanh approximation changes in the sound
against upstream's erf was not measured. Why each change was made is not stated beyond what a
comment in the file says.

## Notices and provenance

**Was wrong.** `runtime/native_pocket/android/overrides/ggml-vulkan.cpp` is ggml's Vulkan backend
with this project's changes. It stood under this repository's Apache-2.0 licence with no
copyright line, no MIT notice, no upstream revision and no statement of what was changed. The
three files beside it said nothing of their origin either.

`PROVENANCE.md` said that `MANIFEST.sha256` inventories the tracked files and that Git binds it,
and nothing more. So it read as a second binding of what Git binds already, and nothing said
where the inputs from outside the repository are recorded.

`scripts/attach_platform_signature.go` refused a staged root that did not begin "id.aiii.voice-",
though nothing else in the tool depends on the plugin's id.

**Now.** `runtime/native_pocket/android/overrides/NOTICE` lists each file of the directory with
where it comes from. The copy of ggml is stated as that, under ggml's MIT licence, whose text is
reproduced whole. Its base is stated: the file the speech engine carries at audio.cpp revision
`3174e6b26f11a0e39b4f150961dce98f43ba860d`, with its sha256, from which the copy differs in
thirteen places (55 lines added, 20 removed) and in nothing else; all thirteen are listed. Two of
them carried no name of this project: a memory barrier before a result is read back through a
host-coherent mapping, and a completion submission that is a real command. ggml's own revision
inside that engine is not recorded, and the notice says so. The three other files are stated as
written for this project. `ggml-vulkan.cpp` gains a three-line head comment with ggml's copyright
line that points at the notice (17,431 lines with it). No recipe in this repository compiles
these files, which the notice says.

`PROVENANCE.md` says what the manifest is for (checking a copy of the source that has no history,
such as a release's source archive), that it adds nothing inside the repository, why every commit
changes it, and that it covers this repository's files only. It lists, each with its one place,
what a release is built from besides them: the plugin kit's pin; the speech engine's revision and
every file of it that a build replaces, each with its notice, and the script that makes the
source each build was given; the other built-in third-party sources with their notices; the
models; and the release's lineage record for every file of every runtime set.

The staged root the signature tool takes is a package's own name: an id under `id.aiii`, a
hyphen, a version. The staged tree must still be the unsigned bundle byte for byte, and the
envelope must still sign that package and that manifest.

`MANIFEST.sha256` is regenerated for the tree of this release.

**Shown.** `tests/test_android_override_notice.py`: the notice lists exactly the files the
directory holds; each of this project's own states its origin and licence; the copy of ggml
states its licence and its base and carries its head comment; ggml's terms are whole; a base
named without the bytes it was compared against is refused; and every identifier in the copy that
carries this project's name is named in the notice's list of changes, so a change added later
fails until the notice states it. The test carries its own faults (a file not listed, a listed
file absent, the terms cut, the copyright line gone, an unstated change, and others) and each is
refused. The copy was compared with diff against the engine's source, whose three pinned files
have the sha256 this tree records for that revision (`tests/fixtures/native_path_upstream`).

Each place `PROVENANCE.md` names was read in the tree.

The signature tool's pattern takes this plugin's root and another plugin's, and refuses a root
with no version, a version of two numbers, another prefix, a capital, and a path. Run on another
plugin's unsigned package and staged tree with an envelope that carries another package's
signature, the tool attached it, and the host's verifier refused the result.

**Not shown.** The lineage record is made when a release is assembled; no release has published
one yet. No valid signature has passed through the changed tool, and there is no test of the tool
in this tree: it runs inside the plugin kit's module, which the tree's tests do not hold.

## The kit pin, and the opens the host sends

**Was wrong.** The tree pinned a kit revision whose packer cannot write a model's "when", which
the package's conditional models need. A package therefore had to be written with a later kit
than its carriers were built on: two kit revisions in one release, and a lineage that could name
only one of them.

`tests/vectors/session_topology.json` was the kit's copy at that pin, which is the host's file
from before the host began declaring the number of the input stream in every open
(`audio.input.stream`). No vector carried that member, and no test replayed an open as the host
builds it.

`scripts/stage_qualified_runtime.py` handed the kit's packer a files budget equal to the tree's
file count. The kit at the revision pinned in this release counts a tree's directories with its
files, as the host's reader does, and admits beside the budget a quarter as many members again.
A tree with more directories than a quarter of its files is refused: both macOS sets, whose
packaged Core ML cache holds 606 files in 365 directories, could not be staged. At the earlier
pin the packer counted files only, so the same tree was staged without a word.

**Now.** `plugin/sdk-source.json`, `plugin/native/go.mod` and `plugin/NATIVE_BUILD.md` name kit
revision `65c4427527d3701668038088c279104dba83a7e5` and its archive: one revision for the
carriers and for writing the package. Between the two revisions the kit's `pkg/aiiosdk`, `go.mod`
and `go.sum` are byte-identical, and `pkg/aiiosdk` is the only package of the kit that the
carrier compiles (`pkg/aiiospkg` is imported by two of its tests).

`tests/vectors/session_topology.json` is the host's file, byte for byte, with "stream": 1 in the
canonical duplex case. At the new pin the kit's `vectors/session_topology.json` is the same file,
and the test holds the copy to the hash stated for it and, where the pinned kit is extracted,
reads the kit's copy and holds it to the same hash.

`tests/vectors/host_session_opens.json` is new: five whole opens as the host builds them, each
with the function it comes from, and the host release they are of. They are a conversation with a
full capture report, an output-only session, a conversation with a playback reference, a meeting
with no report, and a conversation whose report names nothing, in the order one engine process
could see them. `docs/NATIVE_SESSION_CONTRACT.md` says what the test holds.

The staging script's `runtime_pack_limits()` also gives `files_budget`, the least budget under
which files and directories together fit, computed from the inventory's own paths by the kit's
rule, and the packer is given that. What the package declares stays the file count, and the
staged archive is still held to it.

**Shown.** `tests/test_native_output_only.py`:
`test_topology_vectors_are_the_hosts_file_as_it_was_taken`,
`test_native_parser_consumes_host_topology_vectors`,
`test_native_worker_opens_what_the_host_sends` (each open on a fresh worker: frames on another
stream dropped and counted, frames on its own heard, the session finished by the host's handle
and drained) and `test_host_opens_follow_one_another_in_one_engine_process`. The function that
replays an open was shown to fail on a repeated stream number, on own-stream frames passed as
foreign, on a wrong admission and on a refused entry. On Linux with the kit at the new pin the
carrier builds and vets clean and its package's tests pass with the fixture worker, and
`tests/test_plugin_sdk_pin.py`, `tests/test_plugin_carrier_build.py` and
`tests/test_carrier_runtime_binding.py` pass.

The files budget was found by a rehearsal of the macOS release stages: every stage before
staging passed on both sets, and staging failed on both with the packer's sentence ("the budget
of 606 files (-max-files) admits 757, and the least that admits this tree is 777"). Run again at
the commit with the correction, the stages staged both sets.
`tests/test_qualified_runtime_stage.py`
(`test_the_files_budget_admits_the_trees_directories_and_the_declared_count_stays_the_files`):
the rule gives 777 for that tree, the file count where a tree's directories fit a quarter of it
(the Linux and Windows sets' counts among the cases), and for five shapes the least budget and no
less.

**Not shown.** The playback-reference open is refused by the fixture build ("native
playback-reference processing not included in this runtime": a build option), and the test
accepts exactly that refusal or the admission; the accepting branch has run nowhere here. What
has been built and staged on the new pin are rehearsals (last section), not this release's own
sets.

## The privacy check's new category, and what the documents say

**Was wrong.** The privacy check refused a pointer to evidence kept outside the tree, and no
other reference that a reader of the published tree cannot follow. Passages of the documents
contradicted the shipped build: one would have led a builder to embed Metal's shader source,
which a sandboxed start then compiles again every time (20 to 110 s); others gave ten voices
where there are twenty, a host floor of 0.1.8 where assembly refuses anything below 0.1.14, and
component READMEs still written as plans for an engine that has shipped since 0.1.0-beta.7.

**Now.** `scripts/check_public_privacy.py` has one more category beside the one for pointers to
evidence kept outside the tree: a reference to material that is not published. A licence's name,
which can have the shape of such a reference (CC-BY-4.0), is not one.

The documents say: do not embed the Metal shader source, ship the kernels compiled at build time;
twenty presets and seven speaking languages; assembly's real inputs and floors; the three
vocabulary operations; the kit pin by its recorded hash; the engine without an interpreter as
what every released desktop set carries since 0.1.0-beta.7; and when a speaking voice now applies
(the next reply). Sizes that no record in the tree supports are removed.

**Shown.** The new pattern was run over the three published release tags and over the tree this
account starts from, and matched nothing: nothing already published fails the new category. Its
two test cases and the licence-name control pass (`tests/test_public_privacy.py`). The document
tests that pin phrases pass.

**Not shown.** The check is a regression check on text of a known shape, as its own head comment
says, not a guarantee that nothing else of the kind is in the tree.

## Not established

What follows was shown by no section above, and is stated here once.

Unless a section says otherwise, its builds and tests were made on Linux, and its tests ran
against the model-free fixture worker, a stand-in host or a test double. The changed C++ was
built there with GCC and with clang 18, and not for mobile.

On Windows and on macOS, what ran before this tree was made were rehearsals at commits shortly
before the last one. The release's own runs on the three systems are made from this tree and are
reported with the release, not here.

On Windows, an unsigned trial at the commit before the last round of the limits built the real
worker, the session library and the fixture worker with MSVC, and their unit tests passed (59 and
48). Of 147 test cases that drive the fixture worker, 142 passed. Three failed, being tests that
assumed a POSIX pipe, and two skip themselves there. With real models, the Small set passed its
eight stages (the rebind, lifecycle, audit, settings, reply-voice, continuous speech, languages
and corrections), and the Full set its first seven; the Small-CPU set was not run.

A second trial, at the last round of the limits, built all of it with MSVC again. 58 of its 59
unit tests passed. One failed, every time: a test of that round took the first look at which no
model call was in flight for an idle session, and on Windows that look comes before the session's
owners have made their first calls. The test now waits until the count of calls has stood still
(`model_progress_test.cpp`); that it passes on Windows is for the next trial to show. The test
cases that drive the fixture worker then passed there whole, the three corrected for a Windows
pipe among them.

A third trial, at the commit before this account's last correction, which changed two documents
and no code, built all of it with MSVC again. All 59 unit tests passed, the corrected one among
them, and the test cases that drive the fixture worker passed whole. With real models the Small
and the Full set passed every stage, and the Small-CPU set, run there for the first time, passed
every stage but one. That one is the proof of a reply's voice, and it failed in its second part
only: the proof stated for that set the rounding that had been measured on Linux, and the set
measures what the first section states. What was wrong was the statement. The proof's first
part, that a reply which no longer reply precedes is its reference sample for sample, held.

On macOS, at the commit before the last round of the limits, the release stages ran with real
models on both sets, all 27 passing. At the last round of the limits the closeout gate passed
there by its script (727 cases, 242 s), which was the first build of that round by Apple clang.
At the commit before this account's last correction the release stages passed again on both sets
(27 of 27) and the closeout gate passed again (727 cases, 243 s).

Sessions with real models ran in those rehearsals and in the measurements of the first section,
and nowhere else in this account.

No section above shows a run on a person's speech, recorded or live. What a listener hears after
these changes (a saved voice on the next reply, a session that waits for slow storage, a spelling
with its joiners) is not shown.
