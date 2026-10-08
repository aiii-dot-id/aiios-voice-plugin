"""Real dispatcher/ABI/core with deterministic models; no physical audio claim."""
import array
import hashlib
import json
import os
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

from scripts.prove_native_worker_transport import Worker
from tests.native_limits import limits


VECTORS = Path(__file__).parent / 'vectors'
# The host's topology file, copied byte for byte.
TOPOLOGY = json.loads((VECTORS / 'session_topology.json').read_text())
# The opens a host builds today, whole, and where both files were taken from.
HOST = json.loads((VECTORS / 'host_session_opens.json').read_text())


def retire(w):
    if w.p.poll() is None and w.events and not any(e['type'] == 'failure' for e in w.events):
        sid = w.events[-1]['session_id']
        if w.status(sid)['lifecycle'] not in ('closed', 'failed'):
            w.call('close', session_id=sid, mode='abort')
            w.event('session_end', sid)
    assert w.close() == getattr(w, 'expected_exit', 0)


@pytest.fixture
def worker(tmp_path):
    w = Worker(Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']), tmp_path / 'worker')
    try:
        yield w
    finally:
        retire(w)


@pytest.fixture
def held(request, tmp_path, monkeypatch):
    """Fixture writer that pauses once before publishing the selected Ack."""
    gate = tmp_path / 'gate'
    gate.mkdir()
    monkeypatch.setenv('AII_FIXTURE_HOLD_AUDIO_ACK', request.param)
    monkeypatch.setenv('AII_FIXTURE_AUDIO_ACK_GATE', str(gate))
    w = Worker(Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']), tmp_path / 'worker',
               drain=request.param != 'failed')
    w.hold, w.gate = request.param, gate
    try:
        yield w
    finally:
        (gate / 'release').touch()
        retire(w)


def arguments(sid):
    return dict(session_id=sid, output_handle='playback', audio={
        'format': 's16le', 'input': None, 'output': {'rate': 48000, 'channels': 2}})


def open_output(w, sid):
    result, _ = w.call('open', **arguments(sid))
    assert result['audio'] == {'input': None, 'output': {'rate': 24000, 'channels': 1}}
    query = w.settings.get(timeout=2)
    w.configure(query, 768)
    w.event('session_ready', sid)
    state = w.status(sid)
    assert state['input']['state'] == 'absent'
    assert state['input']['admitted_end_sample'] is None
    assert state['input_completion'] is None
    assert state['recognition']['state'] == 'inactive'
    assert not state['recognition']['utterance_open']
    return result


def refuse(w, op, **args):
    w.counter += 1
    w.send({'id': w.counter, 'operation': 'speech.session.' + op, 'arguments': args})
    row = w.replies.get(timeout=2)
    assert row['id'] == w.counter and 'error' in row, row
    return row


def ended(w, sid, synthesis, kind='synthesis_end'):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        for event in w.events:
            if (event['type'], event['session_id'], event.get('synthesis_id')) == (kind, sid, synthesis):
                return event
        time.sleep(.002)
    raise AssertionError('missing terminal synthesis event')


def receipt(w, sid, name, stream, stopped=False):
    deadline = time.monotonic() + 5
    while not any(f['stream'] == stream and f['kind'] == 3 for f in w.frames):
        assert time.monotonic() < deadline
        time.sleep(.002)
    count = 0 if stopped else sum(f['samples'] for f in w.frames if f['stream'] == stream)
    w.call('playback_report', session_id=sid, synthesis_id=name,
           output_stream=stream, rendered_samples=count, terminal=True)


def test_output_only_drains_without_finish_or_fake_completion(worker):
    w = worker
    open_output(w, 'typed')
    refuse(w, 'finish_input', session_id='typed', stream_id='capture', end_sample=0)
    reply, _ = w.call('synthesize', session_id='typed', synthesis_id='reply', text='Recovery.')
    ended(w, 'typed', 'reply')
    w.call('close', session_id='typed', mode='drain')
    time.sleep(.05)
    assert not any(e['type'] == 'session_end' for e in w.events), 'drain must wait for render evidence'
    receipt(w, 'typed', 'reply', reply['output_stream'])
    assert w.event('session_end', 'typed')['status'] == 'completed'
    assert not any(e['type'] in ('input_finished', 'transcript_partial', 'transcript_final') for e in w.events)


def test_output_only_idle_drain_and_duplex_reopen(worker):
    w = worker
    open_output(w, 'idle')
    w.call('close', session_id='idle', mode='drain')
    w.event('session_end', 'idle')
    w.open('duplex')
    assert w.status('duplex')['input']['state'] == 'accepting'
    w.call('finish_input', session_id='duplex', stream_id='capture', end_sample=0)
    w.event('input_finished', 'duplex')
    w.call('close', session_id='duplex', mode='drain')
    w.event('session_end', 'duplex')


def test_output_only_held_inference_interrupt_recover_and_stream_ownership(worker):
    w = worker
    streams = []
    for sid in ('first', 'successor'):
        open_output(w, sid)
        held, _ = w.call('synthesize', session_id=sid, synthesis_id='held-' + sid, text='Hold.')
        w.event('synthesis_start', sid)
        stop, delay = w.call('stop_playback', session_id=sid)
        assert delay < 1 and stop['output_fenced']
        assert w.status(sid)['synthesis']['state'] == 'running'
        _, delay = w.call('cancel_synthesis', session_id=sid)
        assert delay < 1
        ended(w, sid, 'held-' + sid, 'synthesis_cancelled')
        receipt(w, sid, 'held-' + sid, held['output_stream'], True)
        reply, _ = w.call('synthesize', session_id=sid, synthesis_id='recovery-' + sid, text='Recovery.')
        ended(w, sid, 'recovery-' + sid)
        receipt(w, sid, 'recovery-' + sid, reply['output_stream'])
        streams.extend([held['output_stream'], reply['output_stream']])
        w.call('close', session_id=sid, mode='drain')
        w.event('session_end', sid)
    assert streams == sorted(set(streams)) and len(streams) == 4
    introductions = {e['output_stream']: e for e in w.events if e['type'] == 'synthesis_start'}
    assert set(introductions) == set(streams)
    terminal = set()
    for frame in w.frames:
        assert frame['stream'] in introductions
        assert frame['stream'] not in terminal, 'audio followed stream END'
        if frame['kind'] == 3:
            terminal.add(frame['stream'])
    assert terminal == set(streams)


@pytest.mark.parametrize('variant', ['missing_input', 'null_with_handle', 'format_without_handle', 'missing_output'])
def test_ambiguous_topology_refuses_without_reserving_session(worker, variant):
    args = arguments('invalid')
    if variant == 'missing_input':
        del args['audio']['input']
    elif variant == 'null_with_handle':
        args['input_handle'] = 'capture'
    elif variant == 'format_without_handle':
        args['audio']['input'] = {'rate': 16000, 'channels': 1}
    else:
        del args['output_handle']
    refuse(worker, 'open', **args)
    assert not worker.events


def test_output_only_rejects_unexpected_audio(worker):
    w = worker
    open_output(w, 'no-mic')
    w.input.write(struct.pack('>4sB3xIIQI', b'AUD1', 1, 1, 1, 0, 2) + b'\0\0')
    failure = w.event('failure', 'no-mic')
    assert 'no input direction' in str(failure)
    assert not any(e['type'].startswith('transcript_') for e in w.events)
    # The fault is that session's: the engine process stays for the next one
    # and exits cleanly (test_native_fault_scope.py).
    assert w.p.poll() is None
    w.expected_exit = 0


def declared(arguments):
    """The AUD1 stream an open says its input is written on; None if it says none."""
    source = arguments['audio']['input']
    return source.get('stream') if source else None


def admission(case):
    """The audio member of the engine's answer: the entry's own, or the
    shared file's confirmed admission for the topology asked for."""
    return case.get('admission') or next(
        row['result']['audio'] for row in TOPOLOGY['open_admissions']
        if row['requested'] == case['topology'] and row['confirmed'])


def test_topology_vectors_are_the_hosts_file_as_it_was_taken():
    """The copy is held to the host's bytes, not to a description of them.

    This tree cannot read the host's. The hash is the one taken with the copy
    (host_session_opens.json, taken_from), so a copy that drifts from what was
    taken, or is replaced without saying from where, fails here. The kit the
    carrier is built against carries the same file: its pin and its copy's
    hash are stated beside the host's, and where the pinned kit is extracted
    its copy is read and held to that hash. A new pin is a new comparison.
    """
    taken = HOST['taken_from']
    copy = hashlib.sha256((VECTORS / 'session_topology.json').read_bytes()).hexdigest()
    assert copy == taken['host']['topology_vectors_sha256'], \
        'session_topology.json is not the bytes taken from the host (' + taken['host']['release'] + ')'
    root = Path(__file__).resolve().parents[1]
    pin = json.loads((root / 'plugin/sdk-source.json').read_text())
    assert pin['revision'] == taken['kit']['pinned_revision'], \
        'the kit pin moved: compare its vectors/session_topology.json with the copy again and record the result'
    assert taken['kit']['pinned_topology_vectors_sha256'] == copy, \
        "the pinned kit's copy is stated as other bytes than the host's: say what differs, or take the host's"
    kit = root / pin['source'] / taken['kit']['topology_vectors']
    if kit.is_file():
        assert hashlib.sha256(kit.read_bytes()).hexdigest() == taken['kit']['pinned_topology_vectors_sha256'], \
            "the pinned kit's vectors/session_topology.json is not the bytes stated for it"
    canonical = [c for c in TOPOLOGY['open_requests'] if c.get('canonical')]
    # One canonical open for each topology, and the one with an input names
    # the stream it is written on, as the open a host builds today does.
    assert sorted((c['topology'], declared(c['arguments']) or 0) for c in canonical) == \
        [('duplex', 1), ('output_only', 0)]
    # Every open says which of the host's functions build it.
    assert all(c['source']['functions'] for c in HOST['opens'])


@pytest.mark.parametrize('case', TOPOLOGY['open_requests'], ids=lambda case: case['name'])
def test_native_parser_consumes_host_topology_vectors(worker, case):
    """The host's own examples, byte for byte (held to their hash above);
    this production engine needs an audio binding.

    Control-only is the kit's proof-engine capability, not native speech. Its
    explicit refusal here must not be misrepresented as topology conformance
    for a capability this engine does not implement.
    """
    w = worker
    if case['topology'] in ('invalid', 'control_only'):
        refuse(w, 'open', **case['arguments'])
        assert not w.events
        return
    sid = case['arguments']['session_id']
    result, _ = w.call('open', **case['arguments'])
    assert result['audio'] == admission(case)
    w.configure(w.settings.get(timeout=2), 768)
    w.event('session_ready', sid)
    # The stream the open named is the one the engine says it reads its input
    # from; an open that names none, or has no input, is told none.
    assert w.status(sid)['input']['stream'] == declared(case['arguments'])


PCM, END = 1, 3


def frame(kind, stream, seq, start, payload=b''):
    return struct.pack('>4sB3xIIQI', b'AUD1', kind, stream, seq, start, len(payload)) + payload


def speak(w, stream, frames):
    """Whole frames of 1024 samples on one stream, from its sample 0."""
    for seq in range(1, frames + 1):
        w.input.write(frame(PCM, stream, seq, (seq - 1) * 1024, b'\x00\x20' * 1024))
    return frames, frames * 1024


def dropped(w, sid, want):
    """Frames the session has counted as another stream's, once it has counted `want`."""
    deadline = time.monotonic() + 5
    while (count := w.status(sid)['input']['foreign_frames']) < want:
        assert time.monotonic() < deadline, 'frames on another stream were never accounted'
        time.sleep(.01)
    return count


def host_open(w, case, other=None):
    """Send one of the host's opens exactly as written, then do with the
    session what the host does: answer its settings, read its status, write
    its input on the stream the open named, finish it and drain it.

    `other` is a stream that is not this session's (to the engine, audio an
    earlier session left unread). Two frames on it are written first; they
    must be dropped and counted, never heard and never a fault.

    Returns the stream the session's input was declared on. None when the
    session has no input, or when the entry needs a frontend this build was
    made without and the engine said exactly that.
    """
    args = case['arguments']
    sid, source = args['session_id'], args['audio']['input']
    w.counter += 1
    w.send({'id': w.counter, 'operation': 'speech.session.open', 'arguments': args})
    row = w.replies.get(timeout=2)
    assert row['id'] == w.counter, row
    if 'error' in row:
        # Only an entry that needs a frontend may be refused, only in the words
        # of a build made without it, and the refusal leaves no session behind.
        assert 'needs' in case and row['error'] == case['refused_without'], (case['name'], row)
        assert not any(e['session_id'] == sid for e in w.events)
        return None
    result = row['result']
    assert (result['accepted'], result['session_id']) == (True, sid), result
    assert result['audio'] == admission(case), result
    w.configure(w.settings.get(timeout=2), 768)
    w.event('session_ready', sid)
    state = w.status(sid)
    assert state['lifecycle'] == 'open', state
    heard = state['input']
    if source is None:
        assert (heard['state'], heard['stream'], heard['processing']) == ('absent', None, None), heard
        if other is not None:
            speak(w, other, 2)
            assert dropped(w, sid, 2) == 2
    else:
        assert (heard['state'], heard['stream'], heard['foreign_frames']) == ('accepting', source['stream'], 0), heard
        report = heard['processing']
        assert (report['reported'] if report else None) == source.get('processing'), heard
    if source is None or 'needs' in case:
        # No input to finish; or two channels with roles, whose audio is that
        # frontend's to prove (scripts/prove_native_echo_transport.py).
        w.call('close', session_id=sid, mode='drain' if source is None else 'abort')
        assert w.event('session_end', sid)['status'] == ('completed' if source is None else 'aborted')
        assert not any(e['type'] == 'failure' for e in w.events), w.events
        return declared(args)
    if other is not None:
        speak(w, other, 2)
    seq, end = speak(w, source['stream'], 3)
    # The host finishes the input by its handle, at the engine's sample.
    w.call('finish_input', session_id=sid, stream_id=args['input_handle'], end_sample=end)
    w.input.write(frame(END, source['stream'], seq + 1, end))
    final = w.event('transcript_final', sid)
    assert (final['start_sample'], final['end_sample']) == (0, end), final
    assert w.event('input_finished', sid)['end_sample'] == end
    heard = w.status(sid)['input']
    assert (heard['state'], heard['received_end_sample'], heard['foreign_frames']) == \
        ('finished', end, 0 if other is None else 2), heard
    w.call('close', session_id=sid, mode='drain')
    assert w.event('session_end', sid)['status'] == 'completed'
    assert not any(e['type'] == 'failure' for e in w.events), w.events
    return source['stream']


@pytest.mark.parametrize('case', HOST['opens'], ids=lambda case: case['name'])
def test_native_worker_opens_what_the_host_sends(worker, case):
    """Each open a host builds today, alone on a fresh engine process: the
    mode word, the host's handles, the capture report and the stream number
    are all taken, the session opens, and input on the declared stream is
    heard while input on another is dropped."""
    stream = declared(case['arguments'])
    host_open(worker, case, other=None if stream is None else stream + 1)


def test_host_opens_follow_one_another_in_one_engine_process(worker):
    """The entries in order, as one engine process is sent them: every open
    with an input names the next stream number, and what an earlier session
    left unread is the later session's to drop, with or without an input."""
    numbers = [n for n in (declared(c['arguments']) for c in HOST['opens']) if n is not None]
    assert numbers == list(range(1, len(numbers) + 1)), 'a host declares 1, 2, 3 and repeats none'
    earlier = None
    for case in HOST['opens']:
        opened = host_open(worker, case, other=earlier)
        if opened is not None:
            earlier = opened


def test_a_refused_open_leaves_the_session_before_it_as_it_was(worker):
    """An open is checked whole before it changes what a status shows. The
    capture report was kept as soon as it had been read, before the channel
    roles were, so an open refused for its roles left its own report in the
    status of the session that had ended before it."""
    case = HOST['opens'][0]
    kept = case['arguments']
    report = kept['audio']['input']['processing']
    host_open(worker, case)
    before = worker.status(kept['session_id'])
    assert before['input']['processing']['reported'] == report, before

    refused = json.loads(json.dumps(kept))
    refused['session_id'] = 'vs-0000000000000000000000000000000f'
    refused['audio']['input'].update(
        stream=kept['audio']['input']['stream'] + 1, channels=2,
        # Not an order this engine serves, whatever frontend it was built with.
        channel_roles=['playback_reference', 'capture'],
        processing={name: (False if value is True else True if value is False else value)
                    for name, value in report.items() if name != 'tested'} | {'tested': False})
    assert refused['audio']['input']['processing'] != report
    events = len(worker.events)
    row = refuse(worker, 'open', **refused)
    assert row['error'] == 'unsupported input channel roles', row
    assert len(worker.events) == events, worker.events[events:]

    after = worker.status(kept['session_id'])
    assert after['input']['processing'] == before['input']['processing'], after
    assert (after['session_id'], after['lifecycle']) == (before['session_id'], before['lifecycle']), after
    # The stream number the refused open named was not taken either.
    again = json.loads(json.dumps(refused))
    again['session_id'] = 'vs-00000000000000000000000000000010'
    again['audio']['input'].update(channels=1)
    del again['audio']['input']['channel_roles']
    result, _ = worker.call('open', **again)
    assert result['session_id'] == again['session_id'], result
    worker.configure(worker.settings.get(timeout=2), 768)
    worker.event('session_ready', again['session_id'])
    assert worker.status(again['session_id'])['input']['processing']['reported'] == again['audio']['input']['processing']


def post(w, op, **args):
    w.counter += 1
    w.send({'id': w.counter, 'operation': 'speech.session.' + op, 'arguments': args})
    return w.counter


def observed(w, stream, kind):
    deadline = time.monotonic() + 5
    while not any(f['stream'] == stream and f['kind'] == kind for f in w.frames):
        assert time.monotonic() < deadline, 'writer bytes never observed'
        time.sleep(.002)
    return sum(f['samples'] for f in w.frames if f['stream'] == stream)


def holding(w):
    deadline = time.monotonic() + 5
    while not (w.gate / 'held').exists():
        assert time.monotonic() < deadline, 'audio writer never reached its held Ack'
        time.sleep(.002)


def first(w, request, report):
    """The worker admitted the report, then answered a later request first."""
    row = w.replies.get(timeout=2)
    if row['id'] != request:
        w.replies.get(timeout=2)  # leave no stale answer behind a failure
    assert row['id'] == request and 'result' in row, ('report answered before its write was accounted', report, row)
    return row['result']


def unanswered(w, sid, report):
    """Status overtakes a report whose write still withholds its Ack."""
    state = first(w, post(w, 'status', session_id=sid), report)
    holding(w)
    return state


def answer(w, report, stream, samples, terminal):
    row = w.replies.get(timeout=2)
    assert row == {'id': report, 'result': {
        'accepted': True, 'synthesis_id': 'reply', 'output_stream': stream,
        'rendered_samples': samples, 'terminal': terminal}}, row


def settled(w, sid, samples):
    deadline = time.monotonic() + 5
    while (state := w.status(sid))['bookkeeping']['unresolved_generations']:
        assert time.monotonic() < deadline, state
        time.sleep(.002)
    playback = state['playback']
    assert (playback['state'], playback['delivered_samples'], playback['rendered_samples'],
            playback['discarded_samples']) == ('idle', samples, samples, 0), playback


@pytest.mark.parametrize('held', ['pcm', 'end'], indirect=True)
def test_report_inside_unacknowledged_write_is_reconciled_not_refused(held):
    """The host can read PCM/END before the writer publishes its Ack. The
    exact receipt, sent without waiting for synthesis_end, must not be refused
    against stale delivery accounting; it settles once the Ack is accounted."""
    w = held
    end = w.hold == 'end'
    open_output(w, 'race')
    reply, _ = w.call('synthesize', session_id='race', synthesis_id='reply', text='Recovery.')
    stream = reply['output_stream']
    samples = observed(w, stream, 3 if end else 1)
    assert samples == 960
    report = post(w, 'playback_report', session_id='race', synthesis_id='reply',
                  output_stream=stream, rendered_samples=samples, terminal=end)
    state = unanswered(w, 'race', report)
    assert state['playback']['delivered_samples'] == (samples if end else 0)
    assert not any(e['type'] == 'synthesis_end' for e in w.events)
    (w.gate / 'release').touch()
    answer(w, report, stream, samples, end)
    if not end:
        observed(w, stream, 3)
        w.call('playback_report', session_id='race', synthesis_id='reply',
               output_stream=stream, rendered_samples=samples, terminal=True)
    ended(w, 'race', 'reply')
    settled(w, 'race', samples)
    outcomes = [e['outcome'] for e in w.events if e['type'] == 'playback_observation']
    assert outcomes == (['drained'] if end else ['progress', 'drained'])
    w.call('close', session_id='race', mode='drain')
    assert w.event('session_end', 'race')['status'] == 'completed'


@pytest.mark.parametrize('operation', ['stop_playback', 'cancel_synthesis'])
@pytest.mark.parametrize('held', ['pcm', 'end'], indirect=True)
def test_interruption_is_not_held_behind_a_report_awaiting_its_ack(held, operation):
    w = held
    end = w.hold == 'end'
    open_output(w, 'race')
    reply, _ = w.call('synthesize', session_id='race', synthesis_id='reply', text='Recovery.')
    stream = reply['output_stream']
    samples = observed(w, stream, 3 if end else 1)
    report = post(w, 'playback_report', session_id='race', synthesis_id='reply',
                  output_stream=stream, rendered_samples=samples, terminal=end)
    begun = time.monotonic()
    assert first(w, post(w, operation, session_id='race', synthesis_id='reply'), report)['output_fenced']
    assert time.monotonic() - begun < 1
    holding(w)
    (w.gate / 'release').touch()
    answer(w, report, stream, samples, end)
    observed(w, stream, 3)
    if not end:
        w.call('playback_report', session_id='race', synthesis_id='reply',
               output_stream=stream, rendered_samples=samples, terminal=True)
    deadline = time.monotonic() + 5
    while not any(e['type'] in ('synthesis_end', 'synthesis_cancelled') for e in w.events):
        assert time.monotonic() < deadline, 'missing terminal synthesis event'
        time.sleep(.002)
    settled(w, 'race', samples)
    assert [e['outcome'] for e in w.events if e['type'] == 'playback_observation'][-1] == 'stopped'


@pytest.mark.parametrize('held', ['failed'], indirect=True)
def test_report_inside_failed_write_is_refused_after_its_ack(held):
    """A report never credits audio whose write failed; it is still answered."""
    w = held
    w.expected_exit = 1
    open_output(w, 'broken')
    w.output.close()  # the host stops reading; the next audio write fails
    reply, _ = w.call('synthesize', session_id='broken', synthesis_id='reply', text='Recovery.')
    holding(w)
    report = post(w, 'playback_report', session_id='broken', synthesis_id='reply',
                  output_stream=reply['output_stream'], rendered_samples=960, terminal=False)
    state = unanswered(w, 'broken', report)
    assert state['playback']['delivered_samples'] == 0
    (w.gate / 'release').touch()
    row = w.replies.get(timeout=2)
    assert row['id'] == report and row['error'] == 'impossible render progress', row
    failure = w.event('failure', 'broken')
    assert failure['reason'] == 'native pipe write failed' and failure['resources_released'], failure
    assert not any(e['type'] == 'playback_observation' for e in w.events)
    # Refused before its first byte, the write left only whole frames behind,
    # and no reader is left to misread them: only the session failed.
    assert w.p.poll() is None, 'a write refused before its first byte retired the worker'


def exact(f, n):
    data = b''
    while len(data) < n:
        chunk = f.read(n - len(data))
        assert chunk, 'audio output ended inside a frame'
        data += chunk
    return data


def read_frame(w):
    """One whole AUD1 frame, read as the host reads it: header, then payload."""
    magic, kind, stream, _, _, size = struct.unpack('>4sB3xIIQI', exact(w.output, 28))
    assert magic == b'AUD1'
    exact(w.output, size)
    return kind, stream, size // 2


def retired(w, why):
    try:
        return w.p.wait(timeout=6)
    except subprocess.TimeoutExpired:
        pytest.fail(why)


def remainder(w):
    """What is left on the audio output once the worker's end has closed."""
    data = b''
    while chunk := w.output.read(65536):
        data += chunk
    return data


# Which thread a test can make the one that sees a stalled write's deadline. On POSIX the writing thread
# polls and checks its own deadline between polls, so the fixture's seam can leave the deadline to it alone
# ('writer'). On Windows a write is one blocking WriteFile, which only the main loop's cancellation ends: the
# writer never sees its deadline first there, and the order that seam forces does not exist.
SEES_A_WRITE_DEADLINE = ['either'] if os.name == 'nt' else ['writer', 'either']


@pytest.fixture
def stalled(request, tmp_path, monkeypatch):
    """A host that takes audio only when the test reads it. 'writer' leaves an
    expired write to the audio writer's own check (fixture seam); 'either'
    lets the scheduler choose which thread sees the deadline first."""
    if request.param == 'writer':
        monkeypatch.setenv('AII_FIXTURE_AUDIO_DEADLINE', 'writer')
    w = Worker(Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']), tmp_path / 'worker', drain=False)
    w.expected_exit = 1
    try:
        yield w
    finally:
        retire(w)


@pytest.mark.parametrize('stalled', SEES_A_WRITE_DEADLINE, indirect=True)
def test_expired_audio_write_retires_the_worker(stalled):
    """A write past its deadline has part of its frame on the pipe: no later
    byte there could be framed. Whichever thread sees the deadline, the worker
    retires and writes nothing more."""
    w = stalled
    open_output(w, 'stalled')
    reply, _ = w.call('synthesize', session_id='stalled', synthesis_id='reply', text='Flood.')
    assert read_frame(w) == (1, reply['output_stream'], 32768)  # then the host stops reading
    began = time.monotonic()
    failure = w.event('failure', 'stalled', timeout=8)
    # One sentence, whichever thread saw the deadline: the pipe's own, with the limit that passed.
    assert failure['reason'] == ('native pipe write expired: 3000 ms, the time the limits table gives it '
                                 '(audio_write_ms)'), failure
    assert failure['resources_released'], failure
    assert retired(w, 'worker stayed resident after an expired audio write') == 1
    # Where no test states another, the write was given the table's three seconds, as it was when they were typed.
    assert time.monotonic() - began >= 2.9
    torn = remainder(w)
    # What the expired write left is part of one frame and never a whole one. A POSIX pipe took what it could
    # hold: the header and some of the payload. A WriteFile that was cancelled may have moved nothing or any
    # part, so on Windows what is left is held to be short of the frame, and its header, where it is there, to
    # be that frame's.
    if os.name == 'nt' and len(torn) < 28:
        return
    magic, kind, stream, _, _, size = struct.unpack('>4sB3xIIQI', torn[:28])
    assert (magic, kind, stream) == (b'AUD1', 1, reply['output_stream']), torn[:28]
    assert (len(torn) > 28 or os.name == 'nt') and len(torn) < 28 + size, 'expected exactly the frame the expired write began'


@pytest.mark.parametrize('seam', SEES_A_WRITE_DEADLINE)
def test_a_stalled_audio_write_retires_the_worker_at_the_tables_time(tmp_path, monkeypatch, seam):
    """An audio write is given the table's audio_write_ms. It was three seconds
    typed in the pipe, whatever table the worker was handed: stated here as
    1.2 s, a write the host stops taking retires the worker 1.2 s later, and
    well before three."""
    stated = 1.2
    if seam == 'writer':
        monkeypatch.setenv('AII_FIXTURE_AUDIO_DEADLINE', 'writer')
    w = Worker(Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']), tmp_path / 'worker', drain=False,
               limits=limits(audio_write_ms=int(stated * 1000)))
    w.expected_exit = 1
    try:
        open_output(w, 'stalled')
        reply, _ = w.call('synthesize', session_id='stalled', synthesis_id='reply', text='Flood.')
        assert read_frame(w) == (1, reply['output_stream'], 32768)  # then the host stops reading
        began = time.monotonic()
        assert retired(w, 'worker stayed resident after an expired audio write') == 1
        after = time.monotonic() - began
        failure = w.event('failure', 'stalled')
        assert failure['reason'] == ('native pipe write expired: 1200 ms, the time the limits table gives it '
                                     '(audio_write_ms)'), failure
        assert failure['resources_released'], failure
        assert stated - .1 <= after < 2.5, (
            f'the worker retired {after:.2f} s after its audio write stalled; the table gives a write {stated} s')
    finally:
        retire(w)


@pytest.mark.skipif(os.name == 'nt', reason='counts bytes queued in a POSIX pipe')
def test_a_stalled_control_write_retires_the_worker_at_the_tables_time(tmp_path):
    """A write of a line to the carrier is given the table's control_write_ms.
    It was three seconds typed in the pipe, whatever table the worker was
    handed. Stated here as 1.2 s: when the carrier stops taking the worker's
    lines, the write that cannot move retires the worker 1.2 s later, well
    before three, and the worker says in its log which deadline it was."""
    import fcntl
    import termios
    stated = 1.2
    w = Worker(Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']), tmp_path / 'worker',
               limits=limits(control_write_ms=int(stated * 1000)))
    w.expected_exit = 1
    try:
        open_output(w, 'stalled')
        w.reading.clear()  # the carrier stops taking the worker's lines, as one that has stopped running does
        # Each status is answered in a line, and the lines queue in the pipe until it holds no more. From
        # then on a write of the worker's is not moving: the last time the pipe took anything is when that
        # write began. Asking goes on, slowly, so that a pipe that was only slow is filled all the same.
        queued, held, grew = array.array('i', [0]), -1, time.monotonic()
        deadline = grew + 10
        while True:
            assert time.monotonic() < deadline, 'the worker neither filled the control channel nor retired'
            try:
                post(w, 'status', session_id='stalled')
            except (BrokenPipeError, ValueError):
                pass  # the worker has gone, and its exit is read below
            try:
                w.p.wait(timeout=.005 if time.monotonic() - grew < .05 else .05)
                break
            except subprocess.TimeoutExpired:
                pass
            fcntl.ioctl(w.p.stdout.fileno(), termios.FIONREAD, queued)
            if queued[0] != held:
                held, grew = queued[0], time.monotonic()
        after = time.monotonic() - grew
        assert w.p.returncode == 1 and held >= 4096, (w.p.returncode, held)
        assert stated - .15 <= after < 2.5, (
            f'the worker retired {after:.2f} s after its control write stalled; the table gives a write {stated} s')
        # What is left in the pipe is taken here and not by the stand-in's reader of whole lines: a line longer
        # than a pipe takes at once may have been left in part.
        while os.read(w.p.stdout.fileno(), 65536):
            pass
        said = [json.loads(line.removeprefix('AII_VOICE_FAILURE '))['reason']
                for line in (w.out / 'stderr.log').read_text().splitlines() if line.startswith('AII_VOICE_FAILURE ')]
        assert said and set(said) <= {'native pipe write expired: 1200 ms, the time the limits table gives it '
                                      '(control_write_ms)'}, said
    finally:
        w.reading.set()
        retire(w)


def test_a_worker_that_cannot_end_gives_up_at_the_tables_time_and_says_what_it_waited_for(tmp_path):
    """A worker that has begun to end is given the table's retire_ms. It was
    five seconds typed where its main loop ends and five more where its
    threads are joined, and at either the worker ended with status 72 and said
    nothing. Stated here as 1.2 s, with an audio write that the host has
    stopped taking and that its own limit will not end for a minute: the
    worker's input is closed, as its carrier closes it, and 1.2 s later the
    worker ends with 72 and its log says which limit passed, its number, and
    what had not ended."""
    stated = 1.2
    w = Worker(Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']), tmp_path / 'worker', drain=False,
               limits=limits(retire_ms=int(stated * 1000), audio_write_ms=60000))
    w.expected_exit = 72
    try:
        open_output(w, 'ending')
        reply, _ = w.call('synthesize', session_id='ending', synthesis_id='reply', text='Flood.')
        assert read_frame(w) == (1, reply['output_stream'], 32768)  # then the host stops reading
        # The next frame's write begins and cannot move: the pipe holds what it can of it. A worker
        # whose input is closed before that write has begun has nothing in flight and ends at once.
        if os.name == 'nt':
            time.sleep(.5)
        else:
            import fcntl
            import termios
            queued, stalled = array.array('i', [0]), time.monotonic() + 5
            while queued[0] < 32768:
                assert time.monotonic() < stalled, 'the worker began no second frame'
                time.sleep(.01)
                fcntl.ioctl(w.output.fileno(), termios.FIONREAD, queued)
            time.sleep(.1)
        w.p.stdin.close()  # the carrier ends its worker
        began = time.monotonic()
        assert retired(w, 'the worker was still there six seconds after its input was closed') == 72
        after = time.monotonic() - began
        assert stated - .1 <= after < 3.5, (
            f'the worker gave up {after:.2f} s after it began to end; the table gives its end {stated} s')
        said = [json.loads(line.removeprefix('AII_VOICE_FAILURE '))['reason']
                for line in (w.out / 'stderr.log').read_text().splitlines() if line.startswith('AII_VOICE_FAILURE ')]
        passed = 'the worker did not retire in 1200 ms, the time the limits table gives it (retire_ms): '
        assert [reason for reason in said if reason.startswith(passed) and reason.endswith(' had not ended')], said
    finally:
        retire(w)


def test_audio_that_is_not_taken_fails_the_session_at_the_tables_time(tmp_path):
    """Synthesized audio waits for room in the session's bounded queue for the
    table's output_take_ms. It was fifteen seconds typed in the session core,
    whatever table the worker was handed. The worker empties that queue by
    writing the audio to the host, so a carrier holds this wait over
    audio_write_ms: a pipe the host has stopped taking is said by the pipe's
    own deadline first. The table here is stated the other way round, as no
    carrier states one, to see this wait by itself: a write of audio has a
    minute and the queue 1.2 s. The host stops reading, the queue fills, and
    1.2 s later the worker's log says the session failed for this wait, with
    its number and the member."""
    stated = 1.2
    w = Worker(Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']), tmp_path / 'worker', drain=False,
               limits=limits(output_take_ms=int(stated * 1000), audio_write_ms=60000))
    w.expected_exit = 1
    said = lambda: [json.loads(line.removeprefix('AII_VOICE_FAILURE '))['reason']
                    for line in (w.out / 'stderr.log').read_text().splitlines() if line.startswith('AII_VOICE_FAILURE ')]
    try:
        open_output(w, 'untaken')
        reply, _ = w.call('synthesize', session_id='untaken', synthesis_id='reply', text='Flood.')
        assert read_frame(w) == (1, reply['output_stream'], 32768)  # then the host stops reading
        began = time.monotonic()
        while not said():
            assert time.monotonic() - began < 8, 'audio that nobody took was waited for past its time'
            assert w.p.poll() is None, 'the worker ended without saying why'
            time.sleep(.01)
        after = time.monotonic() - began
        assert said() == ['audio consumer did not release bounded output: 1200 ms, the time the limits table '
                          'gives it (output_take_ms)'], said()
        assert stated - .1 <= after < 4, (
            f'the session failed {after:.2f} s after the host stopped reading; the table gives untaken audio {stated} s')
        # The host reads again, so that the write in flight ends and the worker can; its exit reports the failure.
        w.p.stdin.close()
        remainder(w)
        assert retired(w, 'the worker did not end once the host read again') == 1
    finally:
        retire(w)


@pytest.mark.parametrize('stated, said', [
    (dict(audio_write_ms=3000, output_take_ms=15000),
     {'native pipe write expired: 3000 ms, the time the limits table gives it (audio_write_ms)'}),
    (dict(audio_write_ms=60000, output_take_ms=3000),
     {'audio consumer did not release bounded output: 3000 ms, the time the limits table gives it (output_take_ms)'}),
], ids=['the write speaks', 'the queue speaks'])
def test_a_drain_behind_audio_the_host_does_not_take_is_said_by_the_audios_own_limit(tmp_path, stated, said):
    """A write of audio in flight is not a stalled drain: the pipe's own
    deadline holds it to audio_write_ms, and audio waits behind it in the
    session's queue for output_take_ms. Stated here as 1 s for the drain and 3 s
    for the audio: the host stops reading, the session is told to drain, and
    what is said 3 s on is the audio's own sentence, by the write as a carrier
    states a table and by the queue where the table is stated the other way
    round. The drain said "no progress" a second after it began."""
    w = Worker(Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']), tmp_path / 'worker', drain=False,
               limits=limits(drain_idle_ms=1000, input_tail_ms=250, **stated))
    w.expected_exit = 1
    reasons = lambda: [json.loads(line.removeprefix('AII_VOICE_FAILURE '))['reason']
                       for line in (w.out / 'stderr.log').read_text().splitlines() if line.startswith('AII_VOICE_FAILURE ')]
    try:
        open_output(w, 'behind')
        reply, _ = w.call('synthesize', session_id='behind', synthesis_id='reply', text='Flood.')
        assert read_frame(w) == (1, reply['output_stream'], 32768)  # then the host stops reading
        began = time.monotonic()
        w.call('close', session_id='behind', mode='drain')
        while not reasons():
            assert time.monotonic() - began < 20, 'a drain behind untaken audio said nothing'
            time.sleep(.01)
        after = time.monotonic() - began
        assert reasons()[0] in said, reasons()
        assert after >= 2.7, f'it was said {after:.2f} s into the drain; the table gives the audio 3 s and the drain 1'
        # The host reads again, so that a write still in flight can end and the worker with it.
        if not w.p.stdin.closed:
            w.p.stdin.close()
        remainder(w)
        assert retired(w, 'the worker did not end once the host read again') == 1
    finally:
        retire(w)


def test_a_drains_idle_limit_runs_from_when_its_last_write_of_audio_ended(tmp_path):
    """A write of audio in flight holds a drain, and when it ends the drain has
    its idle limit from then. Stated here as 2 s for the drain and a minute
    for a write: the host stops reading, the session is told to drain, and
    twice the idle limit later nothing has failed. The host then reads the
    reply to its end, nothing moves after that, and the drain is called
    stalled 2 s after the last frame was read, within a second."""
    idle = 2
    w = Worker(Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']), tmp_path / 'worker', drain=False,
               limits=limits(drain_idle_ms=idle * 1000, input_tail_ms=250, audio_write_ms=60000, output_take_ms=60500))
    w.expected_exit = 1
    try:
        open_output(w, 'late')
        reply, _ = w.call('synthesize', session_id='late', synthesis_id='reply', text='Flood.')
        assert read_frame(w) == (1, reply['output_stream'], 32768)  # then the host stops reading
        w.call('close', session_id='late', mode='drain')
        time.sleep(2 * idle)
        # Read from the log: a failure's event is not written while a write of audio is still in flight.
        said = [line for line in (w.out / 'stderr.log').read_text().splitlines() if line.startswith('AII_VOICE_FAILURE ')]
        assert not said, f'a drain behind a write in flight was failed: {said}'
        kinds = []

        def host_reads_again():  # to the reply's END; on its own thread, so that a reply that never ends fails here
            while 3 not in kinds:
                kinds.append(read_frame(w)[0])

        reader = threading.Thread(target=host_reads_again, daemon=True)
        reader.start()
        reader.join(timeout=30)
        assert 3 in kinds, 'the reply did not end on the wire once the host read again'
        last = time.monotonic()
        failure = w.event('failure', 'late', timeout=20)
        after = time.monotonic() - last
        assert failure['reason'] == ('native drain made no progress for 2000 ms, the time the limits table '
                                     'gives it (drain_idle_ms)'), failure
        assert idle - .3 <= after <= idle + 1, (
            f'the drain was called stalled {after:.2f} s after its last write of audio ended; the table gives it {idle} s')
    finally:
        retire(w)


@pytest.mark.skipif(os.name == 'nt', reason='counts bytes queued in a POSIX pipe')
def test_audio_pipe_broken_mid_frame_retires_the_worker(tmp_path):
    """A frame larger than the pipe is part-written when the host goes away.
    The write then fails after its first byte: the worker retires."""
    import fcntl
    import termios
    w = Worker(Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']), tmp_path / 'worker', drain=False)
    w.expected_exit = 1
    try:
        open_output(w, 'torn')
        w.call('synthesize', session_id='torn', synthesis_id='reply', text='Flood.')
        queued = array.array('i', [0])
        deadline = time.monotonic() + 2
        while not queued[0]:
            assert time.monotonic() < deadline, 'the first frame never reached the pipe'
            fcntl.ioctl(w.output.fileno(), termios.FIONREAD, queued)
            time.sleep(.001)
        assert queued[0] < 28 + 65536, 'the frame fit the pipe whole'
        w.output.close()  # the host goes away with part of the frame queued
        failure = w.event('failure', 'torn')
        assert retired(w, 'worker stayed resident after a write failed mid-frame') == 1
        assert failure['reason'] == 'native pipe write failed mid-frame' and failure['resources_released'], failure
    finally:
        retire(w)


def test_audio_ack_seam_is_fixture_only():
    cmake = (Path(__file__).resolve().parents[1] / 'runtime/native/session/CMakeLists.txt').read_text()
    assert cmake.count('AII_AUDIO_ACK_TEST_HOOK') == 1
    assert 'aii_voice_worker_fixture PRIVATE AII_WORKER_BACKEND="fixture-native" AII_AUDIO_ACK_TEST_HOOK' in cmake
