"""Real dispatcher/ABI/core with deterministic models; no physical audio claim."""
import os
import struct
import time
from pathlib import Path

import pytest

from scripts.prove_native_worker_transport import Worker


@pytest.fixture
def worker(tmp_path):
    w = Worker(Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']), tmp_path / 'worker')
    try:
        yield w
    finally:
        if w.p.poll() is None and w.events and not any(e['type'] == 'failure' for e in w.events):
            sid = w.events[-1]['session_id']
            if w.status(sid)['lifecycle'] not in ('closed', 'failed'):
                w.call('close', session_id=sid, mode='abort')
                w.event('session_end', sid)
        assert w.close() == getattr(w, 'expected_exit', 0)


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
    w.expected_exit = 1
    w.input.write(struct.pack('>4sB3xIIQI', b'AUD1', 1, 1, 1, 0, 2) + b'\0\0')
    failure = w.event('failure', 'no-mic')
    assert 'no input direction' in str(failure)
    assert not any(e['type'].startswith('transcript_') for e in w.events)
