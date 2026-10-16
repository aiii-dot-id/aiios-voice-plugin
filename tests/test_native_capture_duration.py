"""Production worker/ABI/core, deterministic models; accelerated audio clock, not a soak."""
import os
import struct
import time
from pathlib import Path

import pytest

from scripts.prove_native_worker_transport import Worker


@pytest.fixture
def worker(tmp_path):
    w = Worker(Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']).resolve(), tmp_path / 'worker')
    try:
        yield w
    finally:
        if w.p.poll() is None and w.events:
            sid = w.events[-1]['session_id']
            if w.status(sid)['lifecycle'] not in ('closed', 'failed'):
                w.call('close', session_id=sid, mode='abort')
                w.event('session_end', sid)
        assert w.close() == 0


def open_with(w, sid, minutes):
    query = w.open(sid, False)
    values = {} if minutes is None else {'capture_limit_minutes': minutes}
    w.send({'settings_reply': {**query, 'values': values}})
    ready = w.event('session_ready', sid)
    effective = ready['models']['operator_settings']
    assert effective['capture_limit_minutes'] == (30 if minutes is None else minutes)
    assert w.status(sid)['operator_settings'] == effective
    return query


def feed(w, samples, packet=4096):
    for seq, start in enumerate(range(0, samples, packet), 1):
        payload = b'\x00\x10' * min(packet, samples-start)
        # FileIO.write may be partial on a pipe; never claim an unwritten tail.
        raw = memoryview(struct.pack('>4sB3xIIQI', b'AUD1', 1, 1, seq, start, len(payload)) + payload)
        while raw:
            sent = w.input.write(raw)
            assert sent and sent <= len(raw)
            raw = raw[sent:]
    return seq


def until(predicate):
    deadline = time.monotonic()+10
    while not predicate():
        assert time.monotonic() < deadline, 'worker observation timeout'
        time.sleep(.002)


def drain_reply(w, sid, name):
    admitted, _ = w.call('synthesize', session_id=sid, synthesis_id=name, text='Recovery.')
    stream = admitted['output_stream']
    until(lambda: any(f['kind'] == 3 and f['stream'] == stream for f in w.frames))
    count = sum(f['samples'] for f in w.frames if f['stream'] == stream and f['kind'] == 1)
    assert count == 960
    w.call('playback_report', session_id=sid, synthesis_id=name, output_stream=stream,
           rendered_samples=count, terminal=True)


def test_finite_limit_clips_crossing_packet_and_completes_before_drain(worker):
    w = worker
    open_with(w, 'finite', 1)
    end = 16000*60
    # Non-aligned packet crosses the cap; additional already-in-flight capture
    # must not turn a graceful cutoff into a fault or a spurious second turn.
    seq = feed(w, end+8197, packet=1537)
    w.input.write(struct.pack('>4sB3xIIQI', b'AUD1', 3, 1, seq+1, end+8197, 0))
    complete = w.event('input_finished', 'finite')
    assert complete['end_sample'] == complete['processed_end_sample'] == end
    assert complete['reason'] == 'capture_limit'
    finals = [e for e in w.events if e['type'] == 'transcript_final']
    assert len(finals) == 1 and finals[0]['end_sample'] == end
    assert finals[0]['text'] == 'opening words retained'
    assert finals[0]['sequence'] < complete['sequence']
    assert w.status('finite')['input_completion']['reason'] == 'capture_limit'
    assert w.status('finite')['lifecycle'] == 'open'
    drain_reply(w, 'finite', 'finite-answer')
    w.call('close', session_id='finite', mode='drain')
    ended = w.event('session_end', 'finite')
    assert ended['status'] == 'completed' and ended['input_samples'] == end
    assert not [e for e in w.events if e['type'] == 'failure']


@pytest.mark.parametrize('minutes', [0, 31])
def test_old_boundary_finish_interruption_recovery_and_pinned_settings(worker, minutes):
    w = worker
    q = open_with(w, 'long', minutes)
    # A repeated settings reply cannot change the current session's setting.
    w.send({'settings_reply': {**q, 'values': {'capture_limit_minutes': 1}}})
    end = 16000*60*30+1025
    feed(w, end)
    until(lambda: w.status('long')['input']['received_end_sample'] == end)
    assert w.status('long')['operator_settings']['capture_limit_minutes'] == minutes
    assert not [e for e in w.events if e['type'] == 'input_finished']
    # Exercises the worker's Finish parser, not just core feed and readback.
    w.call('finish_input', session_id='long', stream_id='capture', end_sample=end)
    finished = w.event('input_finished', 'long')
    assert finished['end_sample'] == finished['processed_end_sample'] == end
    assert finished['reason'] == 'finish_input'
    w.call('synthesize', session_id='long', synthesis_id='held', text='Hold.')
    w.event('synthesis_start', 'long')
    stopped, delay = w.call('stop_playback', session_id='long', synthesis_id='held')
    _, cancelled = w.call('cancel_synthesis', session_id='long', synthesis_id='held')
    assert delay < 1 and cancelled < 1
    w.event('synthesis_cancelled', 'long')
    w.call('playback_report', session_id='long', synthesis_id='held',
           output_stream=stopped['output_stream'], rendered_samples=0, terminal=True)
    drain_reply(w, 'long', 'long-recovery')
    w.call('close', session_id='long', mode='drain')
    assert w.event('session_end', 'long')['status'] == 'completed'
    open_with(w, 'next', 1)
    assert w.status('next')['operator_settings']['capture_limit_minutes'] == 1


def test_zero_does_not_disable_abort_or_default(worker):
    w = worker
    open_with(w, 'default', None)
    w.call('close', session_id='default', mode='abort')
    w.event('session_end', 'default')
    open_with(w, 'unlimited', 0)
    end = 16000*60*30+1
    feed(w, end)
    until(lambda: w.status('unlimited')['input']['received_end_sample'] == end)
    _, elapsed = w.call('close', session_id='unlimited', mode='abort')
    assert elapsed < 1
    assert w.event('session_end', 'unlimited')['status'] == 'aborted'


def test_operator_finish_before_positive_limit_remains_exact(worker):
    w = worker
    open_with(w, 'early', 1)
    end = 1025
    w.call('finish_input', session_id='early', stream_id='capture', end_sample=end)
    feed(w, end)
    complete = w.event('input_finished', 'early')
    assert complete['end_sample'] == complete['processed_end_sample'] == end
    assert complete['reason'] == 'finish_input'
    w.call('close', session_id='early', mode='drain')
    assert w.event('session_end', 'early')['status'] == 'completed'
