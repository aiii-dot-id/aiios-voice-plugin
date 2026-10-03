"""Real dispatcher/ABI/core with deterministic models; no physical audio claim."""
import array
import json
import os
import struct
import subprocess
import time
from pathlib import Path

import pytest

from scripts.prove_native_worker_transport import Worker


TOPOLOGY = json.loads((Path(__file__).parent / 'vectors/session_topology.json').read_text())


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


@pytest.mark.parametrize('case', TOPOLOGY['open_requests'], ids=lambda case: case['name'])
def test_native_parser_consumes_host_topology_vectors(worker, case):
    """Same bytes as host/SDK; this production engine needs an audio binding.

    Control-only is the kit's proof-engine capability, not native speech. Its
    explicit refusal here must not be misrepresented as topology conformance
    for a capability this engine does not implement.
    """
    w = worker
    if case['topology'] in ('invalid', 'control_only'):
        refuse(w, 'open', **case['arguments'])
        assert not w.events
        return
    result, _ = w.call('open', **case['arguments'])
    expected = next(row['result']['audio'] for row in TOPOLOGY['open_admissions']
                    if row['requested'] == case['topology'] and row['confirmed'])
    assert result['audio'] == expected
    w.configure(w.settings.get(timeout=2), 768)
    w.event('session_ready', case['arguments']['session_id'])


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


@pytest.mark.parametrize('stalled', ['writer', 'either'], indirect=True)
def test_expired_audio_write_retires_the_worker(stalled):
    """A write past its deadline has part of its frame on the pipe: no later
    byte there could be framed. Whichever thread sees the deadline, the worker
    retires and writes nothing more."""
    w = stalled
    open_output(w, 'stalled')
    reply, _ = w.call('synthesize', session_id='stalled', synthesis_id='reply', text='Flood.')
    assert read_frame(w) == (1, reply['output_stream'], 32768)  # then the host stops reading
    failure = w.event('failure', 'stalled', timeout=8)
    expected = {'native pipe write interrupted/expired'}
    if os.environ.get('AII_FIXTURE_AUDIO_DEADLINE') != 'writer':
        expected.add('native audio write deadline')
    assert failure['reason'] in expected and failure['resources_released'], failure
    assert retired(w, 'worker stayed resident after an expired audio write') == 1
    torn = remainder(w)
    magic, kind, stream, _, _, size = struct.unpack('>4sB3xIIQI', torn[:28])
    assert (magic, kind, stream) == (b'AUD1', 1, reply['output_stream']), torn[:28]
    assert 28 < len(torn) < 28 + size, 'expected exactly the frame the expired write began'


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
