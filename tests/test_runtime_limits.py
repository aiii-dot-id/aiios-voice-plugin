"""A native set's profile states its time limits, and they are the carrier's.

The Go carrier owns the table of limits (plugin/native/limits.go) and reads
it from the signed profile voice-runtime.json. scripts/runtime_limits.py
states the same table for the scripts that write and read that profile. This
file holds the two together and proves what the scripts do with the table:

1. The scripts' member names and their order, defaults, ranges, host
   allowance and margin are the ones in the carrier's source, read here as
   text. The scripts' validation accepts and refuses the tables the carrier's
   own tests accept and refuse, in the carrier's words. The table the carrier
   hands its worker is computed here as the carrier computes it, and the
   tests' own table and the stand-in's are that one.
2. The scripts that put an engine into a set write every member into its
   profile: the defaults, a caller's table, or what the parent states
   unchanged. A table that cannot hold is refused before anything is
   written. A signing rebind carries the parent's profile as it is.
3. Every reader refuses a table that cannot hold, and staging and assembly,
   where a checkpoint becomes a release, refuse a profile that does not
   state every member. Assembly also refuses a set whose wait for its
   worker's readiness does not end inside the start that set declares to the
   host: the one rule that is the scripts' and not the carrier's.

No model, device or compiled artifact is used, and no Go is run. The carrier's
own strict reading of the member is proved on its side (limits_test.go).
"""
import hashlib
import inspect
import io
import json
import re
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import runtime_limits
from scripts.package_native_runtime import runtime_inventory, sha256, verify
from scripts.runtime_limits import (DEFAULTS, MEMBERS, RANGES, complete_limits, default_limits, limits_from,
                                    limits_to_state, profile_limits, read_limits_file,
                                    startup_covers_readiness, worker_environment, worker_table)
from tests import native_limits
from tests.test_native_rebuild_libraries import NEMO, nemo_sources, run_rebuild, sealed_windows_parent

ROOT = Path(__file__).resolve().parents[1]
CARRIER = (ROOT / 'plugin/native/limits.go').read_text(encoding='utf-8')
CARRIER_TESTS = (ROOT / 'plugin/native/limits_test.go').read_text(encoding='utf-8')
WORKER = (ROOT / 'runtime/native/session/worker_limits.h').read_text(encoding='utf-8')
MILLISECONDS = {'Second': 1000, 'Millisecond': 1}

# A profile, or a parent, that states no table at all.
UNSTATED = object()
# A table that is not the defaults, in range and nesting: what a caller might state.
OTHER = dict(host_read_ms=2000, host_write_ms=8000, storage_wait_ms=14000, drain_idle_ms=20000,
             reply_settings_ms=200, abort_ms=6000, capture_close_ms=50000, session_open_ms=70000,
             opening_notice_ms=2000, control_ms=1500, audio_write_ms=4000, ready_ms=240000,
             control_write_ms=5000, retire_ms=4000, capture_tail_ms=3000, worker_exit_ms=6000,
             worker_reap_ms=7000, lane_flush_ms=900, model_call_ms=20000, input_tail_ms=2500,
             output_take_ms=6000, speaker_match_ms=8000, warm_probe_ms=30000, endpoint_decision_ms=700,
             endpoint_retire_ms=21000, separation_min_ms=3000, separation_max_ms=19000)
# What each set of the package declares to the host as the allowance for its
# start (startup_ms, in its accelerator declaration). The carrier's own test
# of its default names the same number.
DECLARED_STARTUP_MS = 180000
# The members only the carrier waits by: a control's answer, its worker's
# readiness, and the three waits of its own end.
CARRIERS_OWN = ('control_ms', 'ready_ms', 'worker_exit_ms', 'worker_reap_ms', 'lane_flush_ms')


# 1. The scripts' table is the carrier's.

def carrier_members():
    """The profile's member names by the carrier's field, in the order the carrier declares them."""
    return dict(re.findall(r'(\w+)\s+\*int64\s+`json:"(\w+),omitempty"`', CARRIER))


def carrier_constant(name):
    count, unit = re.search(r'\b' + name + r'\s*=\s*(\d+) \* time\.(Second|Millisecond)', CARRIER).groups()
    return int(count) * MILLISECONDS[unit]


def carrier_table(literal):
    """A profileLimits literal of the carrier's tests, by the profile's member names."""
    members = carrier_members()
    return {members[field]: int(value) for field, value in re.findall(r'(\w+): ms\((-?\d+)\)', literal)}


def carrier_defaults():
    """The carrier's default for each member of the profile, in milliseconds, from its source."""
    members = carrier_members()
    block = re.search(r'var defaultLimits = limits\{(.*?)\}', CARRIER, re.S).group(1)
    by_field = {field: int(count) * MILLISECONDS[unit]
                for field, count, unit in re.findall(r'(\w+):\s*(\d+) \* time\.(Second|Millisecond)', block)}
    # limitsFrom pairs each member of the profile with the limit it sets.
    sets = dict(re.findall(r'\{p\.(\w+), &l\.(\w+)\}', CARRIER))
    assert set(sets) == set(members) and set(sets.values()) == set(by_field) and len(by_field) == len(members)
    return {members[member]: by_field[field] for member, field in sets.items()}


def test_the_scripts_names_order_and_defaults_are_the_carriers():
    members = carrier_members()
    assert len(members) == 27 and tuple(members.values()) == MEMBERS, (
        'the members the carrier reads are not the ones the scripts write, or not in its order')
    assert carrier_defaults() == DEFAULTS
    assert default_limits() == DEFAULTS and tuple(default_limits()) == MEMBERS


def test_the_scripts_ranges_allowance_and_margin_are_the_carriers():
    # Those the carrier holds to one range. The wait for a worker's readiness has a range of its own, and
    # the two waits a listener feels share another: a reply's for its settings, a turn's for the endpoint's
    # verdict.
    general = re.findall(r'"(\w+_ms)":\s*l\.\w+', CARRIER)
    listeners = {'reply_settings_ms', 'endpoint_decision_ms'}
    assert len(general) == len(MEMBERS) - 3 and set(general) | listeners | {'ready_ms'} == set(MEMBERS)
    for name in general:
        assert RANGES[name] == (carrier_constant('limitFloor'), carrier_constant('limitCeiling')), name
    for name in listeners:
        assert RANGES[name] == (carrier_constant('replyFloor'), carrier_constant('replyCeiling')), name
    assert RANGES['ready_ms'] == (carrier_constant('readyFloor'), carrier_constant('readyCeiling')) == (1000, 3600000)
    assert runtime_limits.HOST_INVOKE_ALLOWANCE_MS == carrier_constant('hostInvokeAllowance')
    assert runtime_limits.MARGIN_MS == carrier_constant('limitMargin')
    assert runtime_limits.HOST_ANSWER_MARGIN_MS == carrier_constant('hostAnswerMargin')


# The carrier's arithmetic, as its source writes it. scripts/runtime_limits.py
# repeats it in Python (storage_operation_ms, playback_report_ms, worker_table
# and the rules of limits_from). A line here that is no longer in the carrier
# means the arithmetic moved: bring the Python with it, then this list.
CARRIER_ARITHMETIC = (
    'if d < limitFloor || d > limitCeiling {',
    'if l.ReplySettings < replyFloor || l.ReplySettings > replyCeiling {',
    'if l.EndpointDecision < replyFloor || l.EndpointDecision > replyCeiling {',
    'if l.Ready < readyFloor || l.Ready > readyCeiling {',
    'if l.HostWrite < l.HostRead {',
    'if l.StorageWait < l.HostWrite {',
    'if l.storageOperation()+hostAnswerMargin > hostInvokeAllowance {',
    'if l.playbackReport()+hostAnswerMargin > hostInvokeAllowance {',
    'if l.WorkerExit < l.Retire+limitMargin {',
    'if l.OutputTake < l.AudioWrite+limitMargin {',
    'if l.DrainIdle < l.InputTail+limitMargin {',
    'if l.Ready < l.WarmProbe+limitMargin {',
    'if l.EndpointRetire < l.ModelCall+limitMargin {',
    'if l.SeparationMax < l.SeparationMin {',
    'if l.ModelCall < l.SeparationMax+limitMargin {',
    'if action == "" { return l.HostRead } return l.HostWrite',
    'func (l limits) workerExchange(action string) time.Duration { return l.query(action) + limitMargin }',
    'func (l limits) opening() time.Duration { return l.StorageWait }',
    'func (l limits) workerOpening() time.Duration { return l.opening() + limitMargin }',
    'func (l limits) wholeRead() time.Duration { return l.StorageWait + limitMargin }',
    'return 2*l.workerExchange("stage") + l.workerExchange("publish") + l.wholeRead()',
    'return 2*l.wholeRead() + l.wholePublication() + l.Control',
    'func (l limits) playbackReport() time.Duration { return l.AudioWrite + l.Control }',
    # What the worker is handed (forWorker), member by member.
    'ExchangeReadMS: l.workerExchange("").Milliseconds(),',
    'ExchangeWriteMS: l.workerExchange("stage").Milliseconds(),',
    'OpeningMS: l.workerOpening().Milliseconds(),',
    'WholeReadMS: l.wholeRead().Milliseconds(),',
    'WholePublicationMS: l.wholePublication().Milliseconds(),',
    'DrainIdleMS: l.DrainIdle.Milliseconds(),',
    'ReplySettingsMS: l.ReplySettings.Milliseconds(),',
    'AbortMS: l.Abort.Milliseconds(),',
    'CaptureCloseMS: l.CaptureClose.Milliseconds(),',
    'SessionOpenMS: l.SessionOpen.Milliseconds(),',
    'OpeningNoticeMS: l.OpeningNotice.Milliseconds(),',
    'AudioWriteMS: l.AudioWrite.Milliseconds(),',
    'ControlWriteMS: l.ControlWrite.Milliseconds(),',
    'RetireMS: l.Retire.Milliseconds(),',
    'CaptureTailMS: l.CaptureTail.Milliseconds(),',
    'ModelCallMS: l.ModelCall.Milliseconds(),',
    'InputTailMS: l.InputTail.Milliseconds(),',
    'OutputTakeMS: l.OutputTake.Milliseconds(),',
    'SpeakerMatchMS: l.SpeakerMatch.Milliseconds(),',
    'WarmProbeMS: l.WarmProbe.Milliseconds(),',
    'EndpointDecisionMS: l.EndpointDecision.Milliseconds(),',
    'EndpointRetireMS: l.EndpointRetire.Milliseconds(),',
    'SeparationMinMS: l.SeparationMin.Milliseconds(),',
    'SeparationMaxMS: l.SeparationMax.Milliseconds(),',
)


def test_the_carriers_arithmetic_is_still_what_the_scripts_repeat():
    source = ' '.join(CARRIER.split())
    moved = [line for line in CARRIER_ARITHMETIC if line not in source]
    assert not moved, 'the carrier no longer writes: ' + ' | '.join(moved)
    # A storage control's own round trip was two seconds typed beside the control limit: it is the limit.
    assert (runtime_limits.storage_operation_ms({**DEFAULTS, 'control_ms': 5000})
            - runtime_limits.storage_operation_ms(DEFAULTS)) == 5000 - DEFAULTS['control_ms']
    # The carrier's own test says what its defaults give a storage control.
    seconds = re.search(r'storageOperation\(\); got != (\d+)\*time\.Second', CARRIER_TESTS).group(1)
    assert runtime_limits.storage_operation_ms(DEFAULTS) == int(seconds) * 1000


def carrier_handed():
    """The members the carrier hands its worker, in the order it writes them."""
    block = re.search(r'^type workerLimits struct \{\n(.*?)^\}', CARRIER, re.S | re.M).group(1)
    return re.findall(r'json:"(\w+)"', block)


def test_what_the_scripts_compute_for_the_worker_is_what_the_carrier_hands_over():
    handed = worker_table(carrier_defaults())
    # Member for member and in the carrier's order: the stand-in's line is then the carrier's, byte for byte.
    assert list(handed) == carrier_handed() and len(handed) == 24
    # The worker's own defaults are what the carrier's defaults compute to (the carrier's side holds that
    # too, in Go: TestTheWorkersDefaultsAreWhatTheCarriersDefaultsComputeTo).
    compiled = {name + '_ms': int(value)
                for name, value in re.findall(r'std::chrono::milliseconds (\w+)\{(\d+)\};', WORKER)}
    assert handed == compiled
    parsed = re.findall(r'\{"([a-z_]+_ms)",\s*&limits\.', WORKER)
    assert sorted(parsed) == sorted(handed), 'the worker does not parse the members the carrier hands over'
    # control_ms, ready_ms and the three waits of the carrier's own end are the carrier's alone; every other
    # stated member reaches the worker, as stated or computed.
    assert set(MEMBERS) - set(handed) == {'host_read_ms', 'host_write_ms', 'storage_wait_ms', *CARRIERS_OWN}
    assert worker_environment({}) == json.dumps(handed, separators=(',', ':'))
    # A stated table moves what is computed from it and nothing else.
    moved = worker_table(dict(host_read_ms=1000, host_write_ms=4000, storage_wait_ms=6000, audio_write_ms=700))
    assert moved == {**handed, 'exchange_read_ms': 1500, 'exchange_write_ms': 4500, 'opening_ms': 6500,
                     'whole_read_ms': 6500, 'whole_publication_ms': 20000, 'audio_write_ms': 700}
    with pytest.raises(ValueError, match="a write must be given at least a read's time"):
        worker_table(dict(host_read_ms=6000, host_write_ms=5000, storage_wait_ms=20000))


def test_the_tests_table_and_the_stand_ins_are_the_one_the_carrier_computes_from_its_defaults():
    # tests/native_limits.py kept a table of its own with values no carrier could hand over (its opening
    # and its whole read differed, and the carrier computes them equal). It is the carrier's default
    # table now, and so is what the stand-in states where a test states none.
    computed = worker_table(carrier_defaults())
    assert native_limits.TABLE == computed and list(native_limits.TABLE) == list(computed)
    assert native_limits.limits() == worker_environment({})
    assert json.loads(native_limits.limits(audio_write_ms=700)) == {**computed, 'audio_write_ms': 700}
    for own in CARRIERS_OWN:  # the worker is never handed them
        with pytest.raises(KeyError):
            native_limits.limits(**{own: 1000})
    # The stand-in: never an inherited table, and the computed one where a test passes none. What a
    # worker it starts is in fact handed is read from the worker's process in tests/test_native_fault_scope.py.
    stand_in = (ROOT / 'scripts/prove_native_worker_transport.py').read_text(encoding='utf-8')
    assert "env.pop('AII_VOICE_LIMITS',None)" in stand_in and 'worker_environment(default_limits())' in stand_in


def test_the_worker_counts_its_own_end_and_a_captures_last_frames_by_the_table():
    # Each is driven with the limit stated: the worker's end in tests/test_native_output_only.py, and a
    # capture's wait for its last frames in tests/test_native_fault_scope.py, through a fixture worker
    # started with a speaker policy. The source is held here beside them: the deadline is the table's,
    # and what is said when it passes names the member.
    worker = (ROOT / 'runtime/native/session/worker.cpp').read_text(encoding='utf-8')
    assert worker.count('capture_tail_deadline_=Clock::now()+uid_snapshot_.limits().capture_tail;') == 1
    assert worker.count('exit_deadline_ = Clock::now() + uid_snapshot_.limits().retire;') == 1
    assert not re.search(r'(capture_tail_deadline_|exit_deadline_)\s*=\s*Clock::now\(\)\s*\+\s*std::chrono', worker)
    said = ' '.join(worker.split())
    assert ('std::to_string(uid_snapshot_.limits().capture_tail.count()) + '
            '" ms after it was told where it ends, the time the limits table gives them (capture_tail_ms)"') in said
    assert ('std::to_string(uid_snapshot_.limits().retire.count()) + '
            '" ms, the time the limits table gives it (retire_ms): "') in said


# EVERY DURATION TYPED IN THE ENGINE'S SOURCES, WITH WHAT IT IS. A wait the
# engine or the worker is held to is a member of the limits table and is
# stated to the code that waits by it; none is typed where it is used. What is
# still typed in runtime/native/session is listed here, file by file, with
# the reason it is not such a wait. A duration typed there and not listed
# fails the test below: it becomes a member of the table, or it is listed with
# what it is. The tests of the directory are not read; its probes are.
#
# What counts as typed: a std::chrono duration built from a number or declared
# with one, a number with a duration's suffix, a number given to a name that
# ends in a unit of time or holds "timeout" or "deadline", and a number such a
# name is compared with. Audio measured in samples is not found by this and is
# not a wait: the longest reply, the longest gap, a capture's least and most.
SESSION_TYPED = {
    # THE TABLE ITSELF: the ranges of what may be stated. Its members and
    # their numbers are the tests' table (WORKERS_TABLE below), which the tests
    # above hold to the carrier's.
    'worker_limits.h': ['ceiling_ms = 600000', 'floor_ms = 250', 'reply_ceiling_ms = 2000', 'reply_floor_ms = 10'],
    # THE SESSION'S SETTINGS, for a caller that states none (a probe, a test):
    # the worker states every wait and the warm inference from the table
    # (test_the_worker_states_the_engines_waits_from_the_table). The pause is
    # the operator's setting, the ceiling a range, and a separation's two
    # bounds are nought where the session states none.
    'session.h': ['default_warm_probe_ms = 40000', 'endpoint_decision_timeout_ms = 1000', 'endpoint_retire_timeout_ms = 30500',
                  'input_tail_timeout_ms = 3000', 'model_call_timeout_ms = 30000', 'output_take_timeout_ms = 15000',
                  'pause_ms = 768', 'separation_maximum_ms = 0', 'separation_minimum_ms = 0',
                  'session_wait_ceiling_ms = 600000'],
    # Ranges of what may be stated, and a reading's first value.
    'session.cpp': ['admitted_ns=0', 'endpoint_decision_timeout_ms>=1', 'endpoint_retire_timeout_ms>=1',
                    'input_tail_timeout_ms>=1', 'model_call_timeout_ms>=1', 'output_take_timeout_ms>=1',
                    'pause_ms<=5000', 'pause_ms>=320', 'separation_minimum_ms>=1'],
    # A caller's own wait is bounded, and a warm inference's time must be stated.
    'c_api.cpp': ['limit_ms>=1', 'ms<=30000'],
    # The operator's settings: the least pause, and how long a recording may be by default.
    'operator_settings.h': ['pause_ms>=320'],
    'capture_limit.h': ['default_capture_limit_minutes = 30'],
    # A time of day that is carried, and a reading's first value.
    'capture_input.h': ['created_ms=0', 'created_ms>0'],
    'android_endpoint_hint.h': ['preferred_rate_ns=0'],
    'worker.cpp': [
        # How long input must have been held before the worker says so in its log; nothing ends at it.
        'std::chrono::milliseconds(1000)',
        # A future asked whether it is ready, without waiting.
        'std::chrono::milliseconds(0)', 'std::chrono::milliseconds(0)', 'std::chrono::milliseconds(0)',
        'std::chrono::milliseconds(0)',
        # How often the main loop looks again while nothing wakes it; no limit is counted in these.
        'std::chrono::milliseconds(idle?(session_?10:100)',
        # How often the worker's end looks at its threads, inside the table's retire.
        'std::chrono::milliseconds(1)',
    ],
    # THE PROBES: programs a developer runs by hand against real models. They
    # are in no engine and no worker; their numbers are how long the probe
    # itself is willing to go on, how often it looks, and its readings' first
    # values.
    'c_api_probe.cpp': ['first_pcm_seconds=0', 'recovery_first_pcm_seconds=0', 'recovery_seconds=0',
                        'std::chrono::milliseconds(1)', 'std::chrono::milliseconds(1)', 'std::chrono::seconds(120)'],
    'causal_turn_probe.cpp': ['1ms'] * 3,
    'continuous_probe.cpp': ['1ms'] * 6,
    'pause_projection_probe.cpp': ['10s'],
    'probe.cpp': ['std::chrono::milliseconds(1)'] * 9,
    'speaker_model_probe.cpp': ['std::chrono::milliseconds(1)'] * 2,
}
SESSION_TESTS = ('c_api_test_models.cpp', 'worker_test_models.cpp', 'worker_exit_test_library.cpp')
A_NUMBER = r"(?<![\w.])\d[\d']*(?![\w.])"
BUILT = re.compile(r'std::chrono::(?:hours|minutes|seconds|milliseconds|microseconds|nanoseconds)\s*[({]([^;{}]*?)[)}]')
SUFFIXED = re.compile(r'(?<![\w.])\d+(?:h|min|s|ms|us|ns)\b(?=\s*[;,)\]}])')
NAMED = re.compile(r"\b\w*(?:_ms|_us|_ns|_seconds|_minutes|timeout\w*|deadline\w*)\s*(?:=|\{)\s*\d[\d']*")
COMPARED = re.compile(r"\b(?:ms|seconds|\w+_ms|\w+_seconds)\s*(?:<=|>=|<|>)\s*\d[\d']*")
# A duration declared by its type and given a number: `std::chrono::milliseconds retire{5000};`, one
# declarator or several.
DECLARED = re.compile(r'std::chrono::(?:hours|minutes|seconds|milliseconds|microseconds|nanoseconds)\s+([^;()]*);')
DECLARATOR = re.compile(r"(\w+)\s*[{=]\s*(\d[\d']*)")


def typed_durations(path):
    """The durations typed in a C or C++ source, its comments left out."""
    code = re.sub(r'/\*.*?\*/', ' ', path.read_text(encoding='utf-8'), flags=re.S)
    code = '\n'.join(line.split('//', 1)[0] for line in code.splitlines())
    found = [match.group(0) for match in BUILT.finditer(code) if re.search(A_NUMBER, match.group(1))]
    found += SUFFIXED.findall(code) + NAMED.findall(code) + COMPARED.findall(code)
    found += ['%s{%s}' % declared for statement in DECLARED.findall(code) for declared in DECLARATOR.findall(statement)]
    return sorted(' '.join(item.split()) for item in found)


def test_a_write_that_has_had_its_time_is_said_by_its_pipe_whichever_thread_sees_it():
    # Two threads can see a write's deadline: the one that is writing, and the worker's main loop. What is
    # said is the pipe's one sentence either way, with the limit that passed and the member its owner
    # named, so tests/test_native_output_only.py asserts that sentence exactly for a stalled write of audio
    # and of a line, whichever thread won. Which wins is the scheduler's, and on Windows a blocked write
    # leaves it to the loop: held here is that the loop has no sentence of its own at any of its four
    # places, and that each pipe is told the member its limit is.
    worker = ' '.join((ROOT / 'runtime/native/session/worker.cpp').read_text(encoding='utf-8').split())
    assert worker.count('fault_transport(wire_.expiry());') == 2 and worker.count('fault_transport(output_.expiry());') == 2
    assert not re.search(r'fault_transport\("[^"]*(?:deadline|expired)', worker)
    assert 'wire_(wire, uid.limits().control_write, "control_write_ms")' in worker
    assert 'output_(audio_descriptor("AII_AUDIO_OUT_FD", false), uid.limits().audio_write, "audio_write_ms")' in worker
    pipe = ' '.join((ROOT / 'runtime/native/session/worker_io.cpp').read_text(encoding='utf-8').split())
    assert ('if (expired()) throw UnframedWrite(expiry()); if (stop) throw UnframedWrite("native pipe write interrupted");') in pipe


# The worker's table as its header declares it: each member of the tests'
# table with its number, and nothing else declared as a duration there.
WORKERS_TABLE = ['%s{%d}' % (member[:-len('_ms')], value) for member, value in native_limits.TABLE.items()]


def test_no_duration_is_typed_in_the_session_directory_but_those_listed_with_a_reason():
    session = ROOT / 'runtime/native/session'
    sources = sorted(path for path in session.iterdir() if path.suffix in ('.cpp', '.h', '.c')
                     and '_test' not in path.name and path.name not in SESSION_TESTS)
    assert len(sources) > 50 and {path.name for path in sources} >= set(SESSION_TYPED)
    for path in sources:
        listed = SESSION_TYPED.get(path.name, []) + (WORKERS_TABLE if path.name == 'worker_limits.h' else [])
        assert typed_durations(path) == sorted(listed), path.name


# THE REST OF WHAT THE DESKTOP WORKER AND ITS LIBRARIES ARE BUILT FROM, by the
# same rule: the directories the native recipes draw on (runtime/native,
# runtime/native/session and runtime/native_uid name them), the echo library's,
# and the speaker model's frontend, each with every directory under it. Each
# directory's tests are not read; its probes are, and a probe is a program a
# developer runs by hand, in no engine and no worker: its numbers are how long
# it is willing to go on, how often it looks and what it holds a measurement
# to. A name of a duration that is given nought is a reading's first value, a
# timer before it has run, and is not listed: a wait is not nought.
COMPONENT_TYPED = {
    'runtime/native/platform': {},
    'runtime/native_asr': {},
    'runtime/native_vad': {},
    'runtime/native_endpoint': {
        # THE PAUSE GATE. 250 ms is what the endpoint's work is asked to take:
        # told to a scheduler and written in a trace, and nothing waits by it
        # or ends at it. Nought is a future asked whether it is ready, without
        # waiting. One second and fifteen are the gate's two waits for a
        # caller that states none, a test: the session states its settings',
        # which the worker takes from the table (endpoint_decision_ms,
        # endpoint_retire_ms).
        'pause_gate.h': ['std::chrono::milliseconds(250)', 'std::chrono::seconds(0)', 'std::chrono::seconds(1)',
                         'std::chrono::seconds(15)'],
        'phase_probe.cpp': ['std::chrono::microseconds(100)'],
        'probe.cpp': ['std::chrono::microseconds(10)'],
        'thread_probe.cpp': ['std::chrono::microseconds(100)'],
    },
    'runtime/native_echo': {},
    'runtime/native_multitalker': {
        # The two bounds of one separation's budget for a caller that states
        # none, a test or a probe: a session states its settings' as it opens,
        # which the worker takes from the table (separation_min_ms,
        # separation_max_ms). The budget's ratio, five times the audio's
        # length, is not a time.
        'separating_recognizer.h': ['default_maximum{25000}', 'default_minimum{4000}'],
        'refinement_lifecycle_probe.cpp': ['seconds<5', 'std::chrono::milliseconds(50)'],
        'separator_execution_probe.cpp': ['admission_ms<=50', 'retirement_ms<=250', 'std::chrono::milliseconds(100)'],
        'source_binding_probe.cpp': ['ms<200', 'ms>=0', 'separator_cancel_ms=1000', 'separator_cancel_ms=25'],
    },
    'runtime/native_uid': {
        # A time of day that is carried: it must have been given.
        'pending_captures.cpp': ['created_ms>0'],
        # How long evidence that is never stored is kept before it is dropped:
        # an age, read when the next evidence comes. Nothing waits by it.
        'profile_admission.h': ['std::chrono::minutes(10)'],
        'session_evidence.h': ['std::chrono::minutes(10)'],
        'probe.cpp': ['control_seconds < 0', 'frontend_cancel_seconds<0', 'frontend_retirement_seconds<0',
                      'retirement_seconds < 0', 'std::chrono::microseconds(50)', 'std::chrono::microseconds(50)',
                      'std::chrono::milliseconds(20)'],
    },
    'runtime/native_uid_ecapa': {},
    # The speech model's resident runtime, and the sources of the engine that
    # a build replaces. They time their own stages and wait on nothing.
    'runtime/native_pocket': {},
    # The speaker model's frontend: the length of a frame of its features and
    # the step between two. The model's own arithmetic, and not a wait.
    'runtime/speaker_identity/native': {'uid_frontend.cc': ['frame_length_ms = 25', 'frame_shift_ms = 10']},
}


# Under those directories and not the desktop worker's: the speech model's
# build for a phone.
NOT_THE_DESKTOP_WORKERS = ('runtime/native_pocket/android',)
A_READING = re.compile(r'\w+\s*(?:=|\{)\s*0')


def test_no_duration_is_typed_in_what_else_the_worker_is_built_from_but_those_listed_with_a_reason():
    for directory, listed in COMPONENT_TYPED.items():
        root = ROOT / directory
        sources = sorted(path for path in root.rglob('*')
                         if path.suffix in ('.cpp', '.h', '.c', '.cc', '.mm', '.cu') and '_test' not in path.name
                         and not path.relative_to(ROOT).as_posix().startswith(tuple(skip + '/' for skip in NOT_THE_DESKTOP_WORKERS)))
        assert sources and {path.relative_to(root).as_posix() for path in sources} >= set(listed), directory
        for path in sources:
            typed = [duration for duration in typed_durations(path) if not A_READING.fullmatch(duration)]
            assert typed == sorted(listed.get(path.relative_to(root).as_posix(), [])), path.relative_to(ROOT).as_posix()
    # A directory of this tree that one of the native recipes comes to draw on is read here too.
    drawn = set()
    for recipe, pattern in (('runtime/native/CMakeLists.txt', r'add_subdirectory\(\.\./(native_\w+)'),
                            ('runtime/native/session/CMakeLists.txt', r'\.\./\.\./(native_\w+)'),
                            ('runtime/native_uid/CMakeLists.txt', r'add_subdirectory\(\.\./(native_\w+)')):
        drawn |= {'runtime/' + name for name in re.findall(pattern, (ROOT / recipe).read_text(encoding='utf-8'))}
    assert drawn >= {'runtime/native_asr', 'runtime/native_endpoint', 'runtime/native_multitalker', 'runtime/native_uid_ecapa'}
    assert drawn <= set(COMPONENT_TYPED), sorted(drawn - set(COMPONENT_TYPED))


def test_the_resident_speech_sources_wait_on_nothing():
    # The session library compiles two sources of the speech model's resident runtime, and one header of
    # that directory with them. None of them types a duration, and none waits, sleeps or keeps a deadline:
    # what a synthesis call may take is the session's model_call_ms, around the call.
    pocket = ROOT / 'runtime/native_pocket'
    cmake = (ROOT / 'runtime/native/session/CMakeLists.txt').read_text(encoding='utf-8')
    # Where the resident library is not handed in already built, it is built from these.
    compiled = re.findall(r'\.\./\.\./native_pocket/(\w+\.cpp)', re.search(r'add_library\(aii_pocket_session STATIC[^)]*\)', cmake).group(0))
    assert compiled == ['resident.cpp', 'assets_bound.cpp']
    included = set(re.findall(r'#include "(\w+\.h)"', ''.join((pocket / name).read_text(encoding='utf-8') for name in compiled)))
    assert included == {'vulkan_device_policy.h'} and (pocket / 'vulkan_device_policy.h').is_file()
    for name in (*compiled, *sorted(included)):
        assert typed_durations(pocket / name) == [], name
        code = '\n'.join(line.split('//', 1)[0] for line in (pocket / name).read_text(encoding='utf-8').splitlines())
        assert not re.findall(r'\b(?:chrono|sleep_for|sleep_until|wait_for|wait_until|timeout|deadline)\w*', code), name


def test_the_worker_states_the_engines_waits_from_the_table():
    # Each of the engine's nine is stated by the worker to the code that waits by it, from the table. Where
    # the fixture worker can reach the limit a test drives it with the limit stated (the model call in
    # tests/test_native_model_progress.py; the conversation's last frames, the final's wait for its
    # speaker and the endpoint's two in tests/test_native_fault_scope.py; the audio that is not taken in
    # tests/test_native_output_only.py). Two the fixture cannot reach. Its warm inference says it took a
    # millisecond: that one is driven in the library's own test (c_api_test.c) and in the carrier's
    # (TestAWarmInferenceIsGivenTheTablesTime). And it has no separating recognizer: a separation's two
    # bounds are driven through a session in separated_recognition_test.cpp and in the recognizer's own
    # test. The worker's statement of all of them is held here.
    worker = ' '.join((ROOT / 'runtime/native/session/worker.cpp').read_text(encoding='utf-8').split())
    for stated in (
            'const aii_voice_session_limits waits{uint32_t(table.model_call.count()), uint32_t(table.input_tail.count()), '
            'uint32_t(table.output_take.count()), uint32_t(table.endpoint_decision.count()), '
            'uint32_t(table.endpoint_retire.count()), uint32_t(table.separation_min.count()), '
            'uint32_t(table.separation_max.count())};',
            'core(aii_voice_open_bounded(models_, &options, &waits, &s, &e), e);',
            'attributions_(uint64_t(uid.limits().speaker_match.count()))',
            'core(aii_voice_models_warm_within(models, uint32_t(uid.limits().warm_probe.count()), &ready, &error), error);'):
        assert worker.count(stated) == 1, stated
    # The entries that take no limit run by the header's numbers: the worker calls neither.
    assert 'aii_voice_open_session(' not in worker and 'aii_voice_models_warm(' not in worker
    # And no model owner holds a number of its own for the warm inference: the library's one check does.
    for owner in ('runtime/native/session/native_c_api.cpp', 'runtime/portability/apple_native_adapter/Sources/native_c_api.cpp'):
        assert not re.search(r'ms\s*>\s*\d', (ROOT / owner).read_text(encoding='utf-8')), owner
    carrier = (ROOT / 'plugin/native/main.go').read_text(encoding='utf-8')
    assert 'int64(r.ProbeMS) > warmProbe.Milliseconds()' in carrier and not re.search(r'ProbeMS\s*>\s*\d', carrier)


# Copied from plugin/native/limits_test.go, by the profile's member names. The
# tables the carrier accepts are those of TestEveryOuterLimitCoversWhatIsInsideIt
# (its first, Go's nil, is a profile that states none), the last millisecond
# of storage that ends inside the host's allowance, the most a playback
# report may be given, the most of a control's own answer that a storage
# control may carry, the ends of the two ranges of their own, and the worker's
# time to retire under the carrier's wait for its exit among them.
CARRIER_ACCEPTS = (
    {},
    dict(host_read_ms=250, host_write_ms=250, storage_wait_ms=250),
    dict(host_read_ms=1500, host_write_ms=1500, storage_wait_ms=2000),
    dict(host_read_ms=1000, host_write_ms=8000, storage_wait_ms=14000),
    dict(host_read_ms=5000, host_write_ms=13000, storage_wait_ms=13000),
    dict(host_read_ms=5000, host_write_ms=13000, storage_wait_ms=14666),
    dict(control_ms=250, audio_write_ms=250),
    dict(control_ms=8000, audio_write_ms=80000, output_take_ms=80500),
    dict(control_ms=13000),
    dict(ready_ms=1000, warm_probe_ms=500, control_write_ms=250),
    dict(ready_ms=3600000, control_write_ms=120000),
    dict(retire_ms=250, worker_exit_ms=750),
    dict(retire_ms=119500, worker_exit_ms=120000),
    dict(capture_tail_ms=250, worker_reap_ms=250, lane_flush_ms=250),
    dict(capture_tail_ms=120000, worker_reap_ms=120000, lane_flush_ms=120000),
    dict(model_call_ms=750, separation_min_ms=250, separation_max_ms=250, input_tail_ms=250, output_take_ms=3500,
         speaker_match_ms=250, warm_probe_ms=250),
    dict(model_call_ms=119500, endpoint_retire_ms=120000, input_tail_ms=14500, output_take_ms=120000,
         speaker_match_ms=120000, warm_probe_ms=120000),
    dict(drain_idle_ms=120000, input_tail_ms=119500),
    dict(audio_write_ms=14500),
    dict(ready_ms=40500),
    dict(endpoint_decision_ms=10),
    dict(endpoint_decision_ms=2000),
    dict(endpoint_retire_ms=30500),
    dict(separation_min_ms=250, separation_max_ms=250),
    dict(separation_min_ms=29500, separation_max_ms=29500),
)
# The tables it refuses are those of TestALimitsTableThatCannotHoldIsRefused,
# each with the words its refusal must carry.
CARRIER_REFUSES = (
    (dict(host_read_ms=0), 'host_read_ms'),
    (dict(host_read_ms=-5), 'host_read_ms'),
    (dict(host_write_ms=249), 'host_write_ms'),
    (dict(storage_wait_ms=120001), 'storage_wait_ms'),
    (dict(drain_idle_ms=100), 'drain_idle_ms'),
    (dict(reply_settings_ms=5), 'reply_settings_ms'),
    (dict(reply_settings_ms=2001), 'reply_settings_ms'),
    (dict(abort_ms=100), 'abort_ms'),
    (dict(capture_close_ms=120001), 'capture_close_ms'),
    (dict(session_open_ms=0), 'session_open_ms'),
    (dict(control_ms=249), 'control_ms'),
    (dict(audio_write_ms=120001), 'audio_write_ms'),
    (dict(control_write_ms=249), 'control_write_ms'),
    (dict(ready_ms=999), 'ready_ms'),
    (dict(ready_ms=3600001), 'ready_ms'),
    (dict(host_read_ms=6000, host_write_ms=5000, storage_wait_ms=20000), "a write must be given at least a read's time"),
    (dict(host_read_ms=1000, host_write_ms=20000, storage_wait_ms=15000), 'must cover one write'),
    (dict(host_read_ms=5000, host_write_ms=30000, storage_wait_ms=60000), 'the host waits 90000 ms'),
    (dict(host_read_ms=5000, host_write_ms=14000, storage_wait_ms=14000), 'the host waits 90000 ms'),
    (dict(host_read_ms=5000, host_write_ms=13000, storage_wait_ms=14667), 'the host waits 90000 ms'),
    (dict(control_ms=8000, audio_write_ms=80001), 'a playback report would be given 88001 ms'),
    (dict(control_ms=10000, audio_write_ms=80000), 'a playback report would be given 90000 ms'),
    (dict(control_ms=13001), 'a control that uses storage would be given 88001 ms'),
    (dict(retire_ms=249), 'retire_ms'),
    (dict(capture_tail_ms=120001), 'capture_tail_ms'),
    (dict(worker_exit_ms=120001), 'worker_exit_ms'),
    (dict(worker_reap_ms=249), 'worker_reap_ms'),
    (dict(lane_flush_ms=0), 'lane_flush_ms'),
    (dict(worker_exit_ms=5499), 'the worker has 5000 ms to retire'),
    (dict(retire_ms=5001), 'this carrier would wait 5500 ms for its worker to exit'),
    (dict(retire_ms=8000, worker_exit_ms=8000), 'the wait must be the longer by 500 ms at least'),
    (dict(model_call_ms=249), 'model_call_ms'),
    (dict(input_tail_ms=120001), 'input_tail_ms'),
    (dict(output_take_ms=0), 'output_take_ms'),
    (dict(speaker_match_ms=249), 'speaker_match_ms'),
    (dict(warm_probe_ms=120001), 'warm_probe_ms'),
    (dict(audio_write_ms=14501), 'the engine would wait 15000 ms for its audio to be taken'),
    (dict(output_take_ms=3499), 'one write of that audio to the host is given 3000 ms'),
    (dict(audio_write_ms=20000, output_take_ms=20000), 'the wait must be the longer by 500 ms at least'),
    (dict(input_tail_ms=14501), 'a drain may go 15000 ms with nothing moving'),
    (dict(drain_idle_ms=3499), "a conversation's last frames are waited for 3000 ms"),
    (dict(ready_ms=40499), "a warm inference is given 40000 ms (warm_probe_ms) and the worker's readiness 40499 ms"),
    (dict(ready_ms=1000), 'readiness must be the longer by 500 ms at least'),
    (dict(endpoint_decision_ms=9), 'endpoint_decision_ms'),
    (dict(endpoint_decision_ms=2001), 'endpoint_decision_ms'),
    (dict(endpoint_retire_ms=120001), 'endpoint_retire_ms'),
    (dict(separation_min_ms=249), 'separation_min_ms'),
    (dict(separation_max_ms=120001), 'separation_max_ms'),
    (dict(endpoint_retire_ms=30499), 'the model call it is has 30000 ms (model_call_ms)'),
    (dict(model_call_ms=30001), "an endpoint question is waited for 30500 ms at the input's end"),
    (dict(endpoint_retire_ms=15000), 'the wait must be the longer by 500 ms at least'),
    (dict(separation_max_ms=29501), 'a separation may take 29501 ms (separation_max_ms)'),
    (dict(model_call_ms=25499, endpoint_retire_ms=30500), 'the model call it runs in is given 25499 ms (model_call_ms)'),
    (dict(separation_min_ms=25001), 'the most must not be the less'),
)


def test_the_copied_tables_are_the_ones_the_carriers_tests_hold():
    body = CARRIER_TESTS.split('func TestEveryOuterLimitCoversWhatIsInsideIt', 1)[1].split('\nfunc ', 1)[0]
    assert 'tables := []*profileLimits{nil,' in body
    accepted = [{}] + [carrier_table(row) for row in re.findall(r'(?m)^\t\t\{((?:\w+: ms\(\d+\)(?:, )?)+)\},', body)]
    assert accepted == list(CARRIER_ACCEPTS), 'the carrier\'s accepted tables changed: copy them here'
    refused = [(carrier_table(row), words)
               for row, words in re.findall(r'\{profileLimits\{([^}]*)\}, "([^"]*)"\}', CARRIER_TESTS)]
    assert refused == list(CARRIER_REFUSES), 'the carrier\'s refused tables changed: copy them here'


@pytest.mark.parametrize('stated', CARRIER_ACCEPTS)
def test_the_scripts_accept_what_the_carrier_accepts(stated):
    table = limits_from(stated)
    # Each member that is left out takes its default, as in the carrier.
    assert table == {**DEFAULTS, **stated} and tuple(table) == MEMBERS


@pytest.mark.parametrize('stated,words', CARRIER_REFUSES)
def test_the_scripts_refuse_what_the_carrier_refuses_in_its_words(stated, words):
    with pytest.raises(ValueError) as refusal:
        limits_from(stated)
    assert words in str(refusal.value)


# The cases of the carrier's TestALimitThisCarrierDoesNotKnowIsRefused, as the
# values a JSON reader hands to Python: a name that is misspelt or in another
# letter case, a null, a string, a fraction, a boolean, and a table that is
# not an object.
@pytest.mark.parametrize('stated,words', [
    (dict(host_reed_ms=2000), 'runtime limit host_reed_ms is not one the carrier knows'),
    ({'HOST_READ_MS': 2000}, 'runtime limit HOST_READ_MS is not one the carrier knows'),
    (dict(host_read_ms=2000, storage_wait=9000), 'runtime limit storage_wait is not one the carrier knows'),
    (dict(host_read_ms=None), 'runtime limit host_read_ms must be a whole number of milliseconds'),
    (dict(host_read_ms='2000'), 'runtime limit host_read_ms must be a whole number of milliseconds'),
    (dict(host_read_ms=2000.5), 'runtime limit host_read_ms must be a whole number of milliseconds'),
    (dict(host_read_ms=2000.0), 'runtime limit host_read_ms must be a whole number of milliseconds'),
    (dict(host_read_ms=True), 'runtime limit host_read_ms must be a whole number of milliseconds'),
    ([], 'runtime limits must be an object'),
    (2000, 'runtime limits must be an object'),
    ('host_read_ms', 'runtime limits must be an object'),
    (None, 'runtime limits must be an object'),
])
def test_the_scripts_read_the_member_as_strictly_as_the_carrier(stated, words):
    with pytest.raises(ValueError) as refusal:
        limits_from(stated)
    assert words in str(refusal.value)


def test_a_complete_table_states_every_member():
    assert complete_limits(default_limits()) == DEFAULTS
    with pytest.raises(ValueError, match='runtime limits do not state abort_ms; every member'):
        complete_limits({name: value for name, value in DEFAULTS.items() if name != 'abort_ms'})
    # A misspelt member is named for what it is, not as the member it left out.
    misspelt = {('host_reed_ms' if name == 'host_read_ms' else name): value for name, value in DEFAULTS.items()}
    with pytest.raises(ValueError, match='runtime limit host_reed_ms is not one the carrier knows'):
        complete_limits(misspelt)


# Tables no reader accepts, with the words each refusal carries.
CANNOT_HOLD = [
    ({**DEFAULTS, 'host_reed_ms': 2000}, 'runtime limit host_reed_ms is not one the carrier knows'),
    ({**DEFAULTS, 'session_open_ms': 120001}, 'runtime limit session_open_ms is 120001 ms; it must be 250 to 120000'),
    ({**DEFAULTS, 'host_read_ms': 13000}, "a write must be given at least a read's time"),
    ({**DEFAULTS, 'host_write_ms': 13000}, 'the whole wait for storage must cover one write'),
    ({**DEFAULTS, 'host_write_ms': 14000, 'storage_wait_ms': 14000}, 'the host waits 90000 ms'),
    (None, 'runtime limits must be an object'),
]
# What is refused besides of a profile on its way into a package: no table,
# and a table that leaves a member out.
RELEASED_REFUSES = [
    (UNSTATED, 'states no time limits'),
    ({}, 'runtime limits do not state host_read_ms, host_write_ms'),
    (dict(host_read_ms=2000), 'runtime limits do not state host_write_ms, storage_wait_ms'),
    *CANNOT_HOLD,
]


def test_what_a_profile_states_is_required_only_of_a_release():
    assert profile_limits({}, released=False) is None
    assert profile_limits(dict(limits=dict(host_read_ms=2000)), released=False) == {**DEFAULTS, 'host_read_ms': 2000}
    assert profile_limits(dict(limits=dict(OTHER)), released=True) == OTHER
    with pytest.raises(ValueError) as refusal:
        profile_limits({}, released=True)
    assert str(refusal.value) == runtime_limits.NOT_STATED and 'states no time limits' in runtime_limits.NOT_STATED


@pytest.mark.parametrize('limits,words', RELEASED_REFUSES)
def test_a_released_profile_states_every_member_and_they_hold(limits, words):
    profile = {} if limits is UNSTATED else dict(limits=limits)
    with pytest.raises(ValueError) as refusal:
        profile_limits(profile, released=True)
    assert words in str(refusal.value)


def test_the_waits_of_a_workers_end_are_stated_and_the_carriers_is_the_outer_by_the_margin():
    # Each was a number typed in the worker or the carrier, and its default is that number, but for one:
    # the carrier waited five seconds for the exit of a worker that had five to retire, and now waits
    # the worker's time and the margin.
    end = ('retire_ms', 'capture_tail_ms', 'worker_exit_ms', 'worker_reap_ms', 'lane_flush_ms')
    assert {name: DEFAULTS[name] for name in end} == dict(
        retire_ms=5000, capture_tail_ms=2000, worker_exit_ms=5500, worker_reap_ms=5000, lane_flush_ms=2000)
    assert DEFAULTS['worker_exit_ms'] == DEFAULTS['retire_ms'] + runtime_limits.MARGIN_MS
    # A parent from before the five were stated: a rebuild writes them at their defaults beside what it states.
    older = {name: value for name, value in OTHER.items() if name not in end}
    grown = limits_to_state(dict(limits=older))
    assert grown == {**DEFAULTS, **older} and tuple(grown) == MEMBERS
    # A table that moves one of the pair and not the other is refused with both numbers.
    with pytest.raises(ValueError, match='would wait 5500 ms for its worker to exit and the worker has 9000 ms to retire'):
        limits_to_state(dict(limits=dict(retire_ms=9000)))


# The one rule that is the scripts' alone: the carrier is not told what its
# set declares to the host for its start, so it cannot hold its wait for
# readiness inside it.

def test_the_default_wait_for_readiness_ends_inside_the_start_a_set_declares():
    declared = re.search(r'declaredStartup = (\d+) \* time\.Second', CARRIER_TESTS).group(1)
    assert int(declared) * 1000 == DECLARED_STARTUP_MS
    # The scripts' default is the carrier's, and it nests with the margin to spare.
    assert DEFAULTS['ready_ms'] == carrier_defaults()['ready_ms'] == 175000
    assert DEFAULTS['ready_ms'] + runtime_limits.HOST_ANSWER_MARGIN_MS <= DECLARED_STARTUP_MS
    startup_covers_readiness(default_limits(), DECLARED_STARTUP_MS)
    # It was the allowance itself, which left the carrier no time to say what it had waited for.
    with pytest.raises(ValueError, match='waits 180000 ms .* declares 180000 ms'):
        startup_covers_readiness({**DEFAULTS, 'ready_ms': DECLARED_STARTUP_MS}, DECLARED_STARTUP_MS)


@pytest.mark.parametrize('ready_ms,startup_ms', [
    (178000, 180000), (175000, 177000), (1000, 3000), (240000, 242000), (3598000, 3600000)])
def test_a_wait_for_readiness_that_ends_inside_the_declared_start_is_accepted(ready_ms, startup_ms):
    assert runtime_limits.HOST_ANSWER_MARGIN_MS == 2000
    startup_covers_readiness({**DEFAULTS, 'ready_ms': ready_ms}, startup_ms)


@pytest.mark.parametrize('ready_ms,startup_ms', [
    (179000, 180000), (178001, 180000), (180000, 180000), (175000, 176999), (175000, 60000), (240000, 180000)])
def test_a_wait_for_readiness_that_outlasts_the_declared_start_is_refused_with_both_numbers(ready_ms, startup_ms):
    with pytest.raises(ValueError) as refusal:
        startup_covers_readiness({**DEFAULTS, 'ready_ms': ready_ms}, startup_ms)
    words = str(refusal.value)
    assert f'waits {ready_ms} ms' in words and '(ready_ms)' in words
    assert f'declares {startup_ms} ms' in words and '(startup_ms)' in words


@pytest.mark.parametrize('startup_ms', [True, 180000.0, '180000', None])
def test_a_declared_start_that_is_not_a_whole_number_is_not_compared(startup_ms):
    with pytest.raises(ValueError, match='startup_ms must be a whole number of milliseconds'):
        startup_covers_readiness(default_limits(), startup_ms)


# 2. The scripts that write a native set's profile.

def limits_file(tmp_path, stated):
    """A caller's limits file: this table as JSON, or these characters as they are."""
    path = tmp_path / 'limits.json'
    path.write_text(stated if isinstance(stated, str) else json.dumps(stated))
    return path


def test_a_callers_file_is_read_whole_and_bound_by_the_bytes_that_were_read(tmp_path):
    path = limits_file(tmp_path, OTHER)
    table, digest = read_limits_file(path)
    assert table == OTHER and tuple(table) == MEMBERS and digest == hashlib.sha256(path.read_bytes()).hexdigest()
    # Whatever order the file is in, the profile is written in the carrier's.
    path.write_text(json.dumps(dict(reversed(list(OTHER.items())))))
    assert read_limits_file(path)[0] == OTHER and tuple(read_limits_file(path)[0]) == MEMBERS


def test_what_a_set_built_from_a_parent_states():
    assert limits_to_state({}) == DEFAULTS and tuple(limits_to_state({})) == MEMBERS
    parent = dict(limits=dict(OTHER))
    carried = limits_to_state(parent)
    assert carried == OTHER and carried is not parent['limits']
    # What the parent states is not moved, and what it leaves out is written at its default: a parent
    # from before a member was added, as every parent is the first time the table grows.
    grown = limits_to_state(dict(limits=dict(host_read_ms=2000)))
    assert grown == {**DEFAULTS, 'host_read_ms': 2000} and tuple(grown) == MEMBERS
    assert limits_to_state(parent, default_limits()) == DEFAULTS
    with pytest.raises(ValueError, match='runtime limits do not state'):
        limits_to_state(parent, dict(host_read_ms=2000))
    with pytest.raises(ValueError, match='runtime limit host_read_ms is 100 ms'):
        limits_to_state(dict(limits={**OTHER, 'host_read_ms': 100}))


def restate(parent, limits):
    """Give a sealed parent's profile this table, and rebind the records that name the profile."""
    path = parent / 'runtime/voice-runtime.json'
    profile = json.loads(path.read_text())
    profile['limits'] = limits
    path.write_text(json.dumps(profile))
    for name in ('freeze.json', 'carrier-build.json'):
        record = json.loads((parent / name).read_text())
        record['runtime_manifest_sha256'] = sha256(path)
        (parent / name).write_text(json.dumps(record))


def rebuilt(tmp_path, monkeypatch, parent, extra=()):
    """Rebuild a sealed parent with a changed NeMo set; the new profile and the new freeze."""
    changed = [argument for source in nemo_sources(tmp_path, NEMO['windows']) for argument in ('--nemo', str(source))]
    out, frozen = run_rebuild(tmp_path, monkeypatch, parent, [*changed, *extra])
    return json.loads((out / 'runtime/voice-runtime.json').read_text()), frozen


def test_a_rebuild_states_every_default_where_its_parent_states_none(tmp_path, monkeypatch):
    parent, _ = sealed_windows_parent(tmp_path)
    assert 'limits' not in json.loads((parent / 'runtime/voice-runtime.json').read_text())
    profile, frozen = rebuilt(tmp_path, monkeypatch, parent)
    assert profile['limits'] == DEFAULTS and tuple(profile['limits']) == MEMBERS
    assert frozen['limits_changed'] is True
    # What it wrote is a profile that staging and assembly go on with.
    assert profile_limits(profile, released=True) == DEFAULTS


def test_a_rebuild_writes_its_callers_table_and_binds_the_file(tmp_path, monkeypatch):
    parent, _ = sealed_windows_parent(tmp_path)
    restate(parent, default_limits())
    table = limits_file(tmp_path, OTHER)
    profile, frozen = rebuilt(tmp_path, monkeypatch, parent, ['--limits', str(table)])
    assert profile['limits'] == OTHER and tuple(profile['limits']) == MEMBERS
    assert frozen['limits_changed'] is True and frozen['bindings'][str(table.resolve())] == sha256(table)


def test_a_rebuild_carries_its_parents_table_unchanged(tmp_path, monkeypatch):
    parent, _ = sealed_windows_parent(tmp_path)
    restate(parent, dict(OTHER))
    profile, frozen = rebuilt(tmp_path, monkeypatch, parent)
    assert profile['limits'] == OTHER and frozen['limits_changed'] is False


@pytest.mark.parametrize('stated,words', [
    ({**OTHER, 'host_reed_ms': 2000}, 'runtime limit host_reed_ms is not one the carrier knows'),
    ({name: value for name, value in OTHER.items() if name != 'abort_ms'}, 'runtime limits do not state abort_ms'),
    ({**OTHER, 'drain_idle_ms': 100}, 'runtime limit drain_idle_ms is 100 ms; it must be 250 to 120000'),
    ({**OTHER, 'host_read_ms': 9000}, "a write must be given at least a read's time"),
    ({**OTHER, 'host_write_ms': 14000}, 'the host waits 90000 ms'),
    ({**OTHER, 'abort_ms': 5000.0}, 'runtime limit abort_ms must be a whole number of milliseconds'),
    ([OTHER], 'runtime limits must be an object'),
    ('{"host_read_ms": 5000, "host_read_ms": 6000}', 'runtime limit host_read_ms is stated twice'),
    ('not json', 'the limits file is not JSON'),
])
def test_a_table_that_cannot_hold_is_refused_before_a_rebuild_writes_anything(tmp_path, monkeypatch, stated, words):
    parent, _ = sealed_windows_parent(tmp_path)
    before = {path: path.read_bytes() for path in parent.rglob('*') if path.is_file()}
    with pytest.raises(ValueError) as refusal:
        rebuilt(tmp_path, monkeypatch, parent, ['--limits', str(limits_file(tmp_path, stated))])
    assert words in str(refusal.value)
    assert not (tmp_path / 'candidate').exists()
    assert {path: path.read_bytes() for path in parent.rglob('*') if path.is_file()} == before


def test_a_parent_whose_table_cannot_hold_is_not_rebuilt_from(tmp_path, monkeypatch):
    parent, _ = sealed_windows_parent(tmp_path)
    restate(parent, {**OTHER, 'host_read_ms': 100})
    with pytest.raises(ValueError, match='runtime limit host_read_ms is 100 ms'):
        rebuilt(tmp_path, monkeypatch, parent)
    assert not (tmp_path / 'candidate').exists()


def mac_stage(tmp_path, monkeypatch, parent_limits, extra=()):
    """Run the Mac stage on a small sealed parent; the directory it was asked to write.

    The Mac's own tools are stood in for: every image has no dependency, and
    nothing is relocated, signed or built. What is exercised is what the
    script copies, what it writes into the profile and when it refuses.
    """
    from scripts import stage_nemotron_macos as stage
    parent, out = tmp_path / 'parent', tmp_path / 'staged'
    runtime = parent / 'runtime'
    held = {'bin/aii_voice_worker': b'parent worker', 'lib/libaii_voice_runtime.dylib': b'parent session library',
            'native-profile.json': json.dumps({'models': {'asr': 'stt'}}).encode(),
            'resources/settings.json': b'[{"key":"fixture"}]\n'}
    for name, raw in held.items():
        (runtime / name).parent.mkdir(parents=True, exist_ok=True)
        (runtime / name).write_bytes(raw)
    (runtime / 'aii-voice-t3').write_bytes(b'parent carrier')
    profile = dict(schema='aiii.voice.native-runtime', platform='darwin', arch='arm64', qualified=False,
                   files=runtime_inventory(runtime, target_platform='darwin'))
    if parent_limits is not UNSTATED:
        profile['limits'] = parent_limits
    (runtime / 'voice-runtime.json').write_text(json.dumps(profile))
    models = tmp_path / 'models'
    (models / 'tts').mkdir(parents=True)
    (models / 'tts/model.bin').write_bytes(b'speech model')
    manifest, carrier = sha256(runtime / 'voice-runtime.json'), sha256(runtime / 'aii-voice-t3')
    (parent / 'carrier-build.json').write_text(json.dumps(dict(carrier_sha256=carrier, runtime_manifest_sha256=manifest)))
    (parent / 'freeze.json').write_text(json.dumps(dict(
        passed=True, signed=False, installed=False, runtime_manifest_sha256=manifest, carrier_sha256=carrier,
        models_root=str(models), models={'tts/model.bin': dict(bytes=12, sha256=sha256(models / 'tts/model.bin'))})))
    build, nemo = tmp_path / 'build', tmp_path / 'nemo'
    build.mkdir()
    (build / 'aii_voice_worker').write_bytes(b'new worker')
    (build / 'libaii_voice_runtime.dylib').write_bytes(b'new session library')
    (nemo / 'lib').mkdir(parents=True)
    (nemo / 'lib/libnemo_speech_asr_c.1.dylib').write_bytes(b'diarizer library')
    (nemo / 'share/licenses/nemo-speech').mkdir(parents=True)
    (nemo / 'share/licenses/nemo-speech/LICENSE').write_bytes(b'license')
    ort, model = tmp_path / 'libonnxruntime.dylib', tmp_path / 'nemotron.gguf'
    ort.write_bytes(b'onnx runtime')
    model.write_bytes(b'diarizer model')

    def settings(worker, runtime, out):
        (out / 'settings.json').write_bytes((runtime / 'resources/settings.json').read_bytes())

    def bind(runtime, record, go):
        (runtime / 'aii-voice-t3').write_bytes(b'rebuilt carrier')
        record.write_text(json.dumps(dict(carrier_sha256=sha256(runtime / 'aii-voice-t3'))))

    monkeypatch.setattr(stage, 'subprocess', SimpleNamespace(check_output=lambda *a, **k: 'image:\n',
                                                             run=lambda *a, **k: None))
    monkeypatch.setattr(stage, 'relocate_macos', lambda path, runtime: None)
    monkeypatch.setattr(stage, 'write_current_settings', settings)
    monkeypatch.setattr(stage, 'bind_carrier', bind)
    monkeypatch.setattr(stage, 'verify_checkpoint', lambda out: None)
    monkeypatch.setattr(sys, 'argv', [
        'stage_nemotron_macos', '--parent', str(parent), '--parent-sha256', sha256(parent / 'freeze.json'),
        '--build', str(build), '--nemo', str(nemo), '--ort', str(ort), '--model', str(model),
        '--model-sha256', sha256(model), '--out', str(out), '--go', str(tmp_path / 'go'), *extra])
    stage.main()
    return out


@pytest.mark.parametrize('parent_limits,table,written', [
    (UNSTATED, None, DEFAULTS),
    (UNSTATED, OTHER, OTHER),
    (dict(OTHER), None, OTHER),
    (dict(OTHER), DEFAULTS, DEFAULTS),
    # A parent from before two members were added: what it states is kept, the two are written at their defaults.
    (dict(host_read_ms=2000, host_write_ms=8000), None, {**DEFAULTS, 'host_read_ms': 2000, 'host_write_ms': 8000}),
])
def test_the_mac_stage_states_the_limits_as_a_rebuild_does(tmp_path, monkeypatch, parent_limits, table, written):
    extra = [] if table is None else ['--limits', str(limits_file(tmp_path, table))]
    out = mac_stage(tmp_path, monkeypatch, parent_limits, extra)
    profile = json.loads((out / 'runtime/voice-runtime.json').read_text())
    assert profile['limits'] == written and tuple(profile['limits']) == MEMBERS
    if table is not None:
        bound = json.loads((out / 'freeze.json').read_text())['bindings']
        assert bound[str((tmp_path / 'limits.json').resolve())] == sha256(tmp_path / 'limits.json')


def test_the_mac_stage_refuses_a_table_that_cannot_hold_before_it_writes_anything(tmp_path, monkeypatch):
    table = limits_file(tmp_path, {**OTHER, 'host_reed_ms': 2000})
    with pytest.raises(ValueError, match='runtime limit host_reed_ms is not one the carrier knows'):
        mac_stage(tmp_path, monkeypatch, UNSTATED, ['--limits', str(table)])
    assert not (tmp_path / 'staged').exists()


def signing_rebind(tmp_path, monkeypatch, limits):
    """Rebind a signed Windows stage of a small parent; the parent's profile and the rebound one.

    The operating system's trust check and the carrier's build are stood in
    for. The files, the inventories and the signing-only comparison are real.
    """
    from scripts import rebind_signed_windows_runtime as module
    from scripts.windows_signing_targets import CORE_IMAGES
    from tests.test_signed_windows_rebind import images
    parent, stage, out = tmp_path / 'parent', tmp_path / 'signed', tmp_path / 'rebound'
    unsigned, signed = images()
    for directory, raw in ((parent, unsigned), (stage, signed)):
        for name in CORE_IMAGES:
            (directory / 'runtime' / name).parent.mkdir(parents=True, exist_ok=True)
            (directory / 'runtime' / name).write_bytes(raw)
        (directory / 'runtime/resources').mkdir()
        (directory / 'runtime/resources/settings.json').write_text('{}\n')
        (directory / 'runtime/aii-voice-t3.exe').write_bytes(b'parent carrier')
    profile = dict(schema='aiii.voice.native-runtime', qualified=False, platform='windows', arch='amd64',
                   files=runtime_inventory(parent / 'runtime', target_platform='windows'))
    if limits is not UNSTATED:
        profile['limits'] = limits
    for directory in (parent, stage):
        (directory / 'runtime/voice-runtime.json').write_text(json.dumps(profile) + '\n')
    binding, carrier = sha256(parent / 'runtime/voice-runtime.json'), sha256(parent / 'runtime/aii-voice-t3.exe')
    (parent / 'carrier-build.json').write_text(json.dumps(dict(
        sdk_revision='pinned', inputs={}, runtime_manifest_sha256=binding, carrier_sha256=carrier)))
    (parent / 'freeze.json').write_text(json.dumps(dict(
        passed=True, signed=False, installed=False, runtime_manifest_sha256=binding, carrier_sha256=carrier,
        library_hashes={})))
    after = runtime_inventory(stage / 'runtime', target_platform='windows')
    (stage / 'result.json').write_text(json.dumps(dict(
        passed=True, parent_runtime_sha256=binding, t3_signed=False, runtime_rebound=False, carrier_rebuilt=False,
        qualified_after_signing=False,
        signed_files=[dict(path=name, before_sha256=profile['files'][name]['sha256'], sha256=after[name]['sha256'],
                           bytes=after[name]['bytes'], subject=module.SUBJECT, timestamp_subject='fixture')
                      for name in sorted(CORE_IMAGES)])))

    def bind(runtime, record, go):
        (runtime / 'aii-voice-t3.exe').write_bytes(b'rebuilt carrier')
        record.write_text(json.dumps(dict(sdk_revision='pinned', inputs={},
                                          carrier_sha256=sha256(runtime / 'aii-voice-t3.exe'))))

    monkeypatch.setattr(module, 'verify_authenticode', lambda runtime, names, signtool: [])
    monkeypatch.setattr(module, 'bind_carrier', bind)
    monkeypatch.setattr(module, 'verify_checkpoint', lambda out: None)
    module.prepare(parent, stage, out, tmp_path / 'go', tmp_path / 'signtool')
    return profile, json.loads((out / 'runtime/voice-runtime.json').read_text())


@pytest.mark.parametrize('limits', [UNSTATED, default_limits(), dict(OTHER), dict(host_read_ms=2000)])
def test_a_signing_rebind_carries_the_parents_profile_as_it_is(tmp_path, monkeypatch, limits):
    parent, rebound = signing_rebind(tmp_path, monkeypatch, limits)
    # Only the inventory is the rebind's: the signed images' new bytes.
    assert {name: value for name, value in rebound.items() if name != 'files'} == {
        name: value for name, value in parent.items() if name != 'files'}
    assert rebound['files'] != parent['files']
    assert ('limits' in rebound) is (limits is not UNSTATED)
    if limits is not UNSTATED:
        assert rebound['limits'] == limits and tuple(rebound['limits']) == tuple(limits)


# 3. The readers.

def sealed_runtime(root, limits):
    """A small sealed runtime whose profile states this table; the profile and its digest."""
    (root / 'bin').mkdir(parents=True)
    (root / 'bin/aii_voice_worker').write_bytes(b'native worker')
    profile = dict(schema='aiii.voice.native-runtime', qualified=False, platform='linux', arch='amd64',
                   backend='native', native='bin/aii_voice_worker', python='', bootstrap='', site='',
                   files=runtime_inventory(root, target_platform='linux'))
    if limits is not UNSTATED:
        profile['limits'] = limits
    (root / 'voice-runtime.json').write_text(json.dumps(profile, indent=2) + '\n')
    return profile, sha256(root / 'voice-runtime.json')


@pytest.mark.parametrize('limits', [UNSTATED, default_limits(), dict(OTHER), dict(host_read_ms=2000), {}])
def test_every_reader_reads_a_profile_from_before_the_table_and_one_whose_table_holds(tmp_path, limits):
    profile, digest = sealed_runtime(tmp_path, limits)
    assert verify(tmp_path, digest) == profile


@pytest.mark.parametrize('limits,words', CANNOT_HOLD)
def test_every_reader_refuses_a_table_that_cannot_hold(tmp_path, limits, words):
    _, digest = sealed_runtime(tmp_path, limits)
    with pytest.raises(ValueError) as refusal:
        verify(tmp_path, digest)
    assert words in str(refusal.value)


class PastTheLimits(Exception):
    """Staging went on past its check of the profile's limits."""


def stage_main(tmp_path, monkeypatch, limits):
    """Run staging on a checkpoint that is ready but for what its profile says of its limits."""
    from scripts import stage_qualified_runtime as stage
    checkpoint, audit = tmp_path / 'checkpoint', tmp_path / 'audit.json'
    _, digest = sealed_runtime(checkpoint / 'runtime', limits)
    (checkpoint / 'runtime/aii-voice-t3').write_bytes(b'carrier')
    (checkpoint / 'freeze.json').write_text(json.dumps(dict(runtime_manifest_sha256=digest)))
    audit.write_text(json.dumps(dict(passed=True, runtime_manifest_sha256=digest)))

    def went_on(*arguments):
        raise PastTheLimits

    # The first thing staging does once the limits are accepted. Nothing of
    # the SDK, of Go or of the carrier is reached.
    monkeypatch.setattr(stage, 'composition_coordinates', went_on)
    monkeypatch.setattr(sys, 'argv', [
        'stage_qualified_runtime', '--checkpoint', str(checkpoint), '--audit', str(audit),
        '--audit-sha256', sha256(audit), '--runtime-sha256', digest, '--out', str(tmp_path / 'staged'),
        '--go-modcache', str(tmp_path / 'modcache'), '--max-compressed-bytes', '1000000'])
    stage.main()


@pytest.mark.parametrize('limits', [default_limits(), dict(OTHER)])
def test_staging_goes_on_with_a_profile_that_states_every_member(tmp_path, monkeypatch, limits):
    with pytest.raises(PastTheLimits):
        stage_main(tmp_path, monkeypatch, limits)


@pytest.mark.parametrize('limits,words', RELEASED_REFUSES)
def test_staging_refuses_a_profile_that_does_not_state_every_limit_and_hold(tmp_path, monkeypatch, limits, words):
    with pytest.raises(ValueError) as refusal:
        stage_main(tmp_path, monkeypatch, limits)
    assert words in str(refusal.value)
    assert not (tmp_path / 'staged').exists()


def digest_of(raw):
    return hashlib.sha256(raw).hexdigest()


def staged_runtime(stage, limits):
    """A stage as staging leaves it: its receipt, and the archive the receipt binds. The profile."""
    stage.mkdir()
    worker = b'native worker'
    files = {'bin/aii_voice_worker': dict(sha256=digest_of(worker), bytes=len(worker), executable=True)}
    profile = dict(schema='aiii.voice.native-runtime', qualified=False, platform='linux', arch='amd64',
                   backend='native', native='bin/aii_voice_worker', python='', bootstrap='', site='', files=files)
    if limits is not UNSTATED:
        profile['limits'] = limits
    raw = (json.dumps(profile, indent=2) + '\n').encode()
    rows = {**files, 'voice-runtime.json': dict(sha256=digest_of(raw), bytes=len(raw), executable=False)}
    inventory = json.dumps({'files': [
        dict(path=name, size=row['bytes'], sha256='sha256:' + row['sha256'], mode='exec' if row['executable'] else 'file')
        for name, row in sorted(rows.items())]}).encode()
    archive = stage / 'linux-x86_64-native-runtime.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name in ('runtime/', 'runtime/bin/'):
            entry = tarfile.TarInfo(name)
            entry.type, entry.mode = tarfile.DIRTYPE, 0o755
            tar.addfile(entry)
        for name, data, mode in (('runtime/inventory.json', inventory, 0o644),
                                 ('runtime/bin/aii_voice_worker', worker, 0o755),
                                 ('runtime/voice-runtime.json', raw, 0o644)):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(data), mode
            tar.addfile(entry, io.BytesIO(data))
    declaration = dict(path=archive.name, sha256=digest_of(archive.read_bytes()), size=archive.stat().st_size,
                       inventory_sha256=digest_of(inventory), files=len(rows),
                       installed_bytes=sum(row['bytes'] for row in rows.values()),
                       largest_file_bytes=max(row['bytes'] for row in rows.values()), depth=2)
    (stage / 'result.json').write_text(json.dumps(dict(
        passed=True, installed=False, published=False, runtime_manifest_sha256=digest_of(raw),
        runtime_archive=declaration)))
    return profile


@pytest.mark.parametrize('limits', [default_limits(), dict(OTHER)])
def test_assembly_reads_a_stage_whose_profile_states_every_member(tmp_path, limits):
    from scripts import assemble_guided_beta_candidate as assembly
    profile = staged_runtime(tmp_path / 'stage', limits)
    result, read = assembly.runtime(tmp_path / 'stage')
    assert read == profile and read['limits'] == limits and result['passed'] is True


@pytest.mark.parametrize('limits,words', RELEASED_REFUSES)
def test_assembly_refuses_a_stage_whose_profile_does_not_state_every_limit_and_hold(tmp_path, limits, words):
    from scripts import assemble_guided_beta_candidate as assembly
    staged_runtime(tmp_path / 'stage', limits)
    with pytest.raises(ValueError) as refusal:
        assembly.runtime(tmp_path / 'stage')
    # The refusal says what is wrong and which stage it is.
    assert words in str(refusal.value) and str(refusal.value).endswith(str(tmp_path / 'stage'))


def declared_sets(startups):
    """Sets as assembly holds them once their declarations are checked: each one's name and its startup_ms."""
    return [dict(variant_id=name, accelerator=dict(startup_ms=startup)) for name, startup in startups.items()]


def waiting(ready_ms):
    """A set's profile whose table is the defaults but for its wait for readiness."""
    return dict(limits={**DEFAULTS, 'ready_ms': ready_ms})


def test_assembly_refuses_a_set_whose_wait_for_readiness_outlasts_the_start_it_declares():
    from scripts import assemble_guided_beta_candidate as assembly
    sets = declared_sets({'linux-cpu': DECLARED_STARTUP_MS, 'windows-gpu': DECLARED_STARTUP_MS, 'macos-gpu': 300000})
    holds = {'linux-cpu': dict(limits=default_limits()), 'windows-gpu': waiting(178000), 'macos-gpu': waiting(298000)}
    assembly.readiness_inside_startup(sets, holds)
    # One second more than leaves the carrier its two seconds: refused, with both numbers and the set.
    with pytest.raises(ValueError) as refusal:
        assembly.readiness_inside_startup(sets, {**holds, 'windows-gpu': waiting(179000)})
    words = str(refusal.value)
    assert 'waits 179000 ms' in words and 'declares 180000 ms' in words and words.endswith(': windows-gpu')
    # Each set is held to what it declares itself, not to what another declares.
    with pytest.raises(ValueError, match='waits 298000 ms .* declares 180000 ms .*: linux-cpu$'):
        assembly.readiness_inside_startup(sets, {**holds, 'linux-cpu': waiting(298000)})
    # A set that declares less than the default wait is refused too: the declaration moved and the profile did not.
    with pytest.raises(ValueError, match='waits 175000 ms .* declares 60000 ms .*: linux-cpu$'):
        assembly.readiness_inside_startup(declared_sets({'linux-cpu': 60000}), holds)
    # The table compared is the one the profile states, every member of it: none is taken from a default here.
    with pytest.raises(ValueError, match='states no time limits.*: linux-cpu$'):
        assembly.readiness_inside_startup(sets, {**holds, 'linux-cpu': {}})
    unstated = {name: value for name, value in DEFAULTS.items() if name != 'ready_ms'}
    with pytest.raises(ValueError, match='runtime limits do not state ready_ms.*: linux-cpu$'):
        assembly.readiness_inside_startup(sets, {**holds, 'linux-cpu': dict(limits=unstated)})


def test_assembly_applies_the_rule_where_it_has_both_the_profiles_and_the_declarations():
    from scripts import assemble_guided_beta_candidate as assembly
    # The profiles are read stage by stage and the declarations are checked by release_contract, which
    # is given no profile: only main has both. The rule follows that check at once, before any output.
    main = ' '.join(inspect.getsource(assembly.main).split())
    assert ("release_contract(cfg, a.minimum_host_version, measured_reservations(bindings, bound)) "
            "readiness_inside_startup(cfg['variants'], profiles)") in main
    assert main.index('readiness_inside_startup(') < main.index('out.mkdir(')
