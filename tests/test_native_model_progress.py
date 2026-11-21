import os
import queue
import subprocess
import time
from pathlib import Path

from scripts.prove_native_worker_transport import Worker
from tests.native_limits import limits


def open_output(w, sid):
    w.call('open', session_id=sid, output_handle='playback', audio={
        'format': 's16le', 'input': None, 'output': {'rate': 24000, 'channels': 1}})
    w.configure(w.settings.get(timeout=2), 768)
    w.event('session_ready', sid)


def test_stalled_models_fault_and_retire_before_recovery(tmp_path):
    binary = Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE'])
    workers = []
    try:
        for label, text in [('cooperative', 'Hold.'), ('stubborn', 'Stall.')]:
            w = Worker(binary, tmp_path / label)
            workers.append(w)
            open_output(w, label)
            w.call('synthesize', session_id=label, synthesis_id='held', text=text)
            w.event('synthesis_start', label)
        cooperative, stubborn = workers
        started = time.monotonic()
        refused_while_retiring = False
        while time.monotonic() - started < 40:
            if stubborn.p.poll() is not None:
                break
            for label, w in [('cooperative', cooperative), ('stubborn', stubborn)]:
                if w.p.poll() is None:
                    try:
                        state = w.status(label)
                    except (BrokenPipeError, OSError, queue.Empty):
                        assert w.p.wait(timeout=5) == (72 if w is stubborn else 1)
                        break
                    assert state['lifecycle'] in ('open', 'draining', 'failed')
                    if w is stubborn and state['lifecycle'] != 'open' and not refused_while_retiring:
                        w.counter += 1
                        w.send({'id': w.counter, 'operation': 'speech.session.open', 'arguments': {'session_id': 'late'}})
                        row = w.replies.get(timeout=2)
                        assert row['id'] == w.counter and 'retiring' in str(row.get('error')), row
                        refused_while_retiring = True
            time.sleep(.2)
        assert stubborn.p.poll() == 72, 'unretired inference must end the process nonzero'
        assert refused_while_retiring, 'the retiring worker was never asked to serve, so its refusal was not shown'
        assert time.monotonic() - started >= 29, 'failed before the production call deadline'
        failure = cooperative.event('failure', 'cooperative')
        assert failure['reason'] == ('synthesis model call exceeded progress deadline: 30000 ms, the time the '
                                     'limits table gives it (model_call_ms)')
        assert failure['resources_released'] is True
        assert not stubborn.frames and not cooperative.frames
        assert not any(e['type'] == 'session_end' for e in stubborn.events)
        assert not any(e.get('resources_released') for e in stubborn.events)
        try:
            assert cooperative.p.wait(timeout=15) == 1, 'the worker that retired after an engine failure exited 0'
        except subprocess.TimeoutExpired:
            raise AssertionError('the worker went on serving after its model call passed its deadline')
    finally:
        for index, w in enumerate(workers):
            if w.p.poll() is None and index == 1:
                w.p.kill()
            w.close()


def test_a_model_call_is_given_the_tables_time(tmp_path):
    stated = 1.5
    w = Worker(Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']), tmp_path / 'held',
               limits=limits(model_call_ms=int(stated * 1000)))
    try:
        open_output(w, 'held')
        w.call('synthesize', session_id='held', synthesis_id='reply', text='Hold.')
        w.event('synthesis_start', 'held')
        began = time.monotonic()
        failure = w.event('failure', 'held', timeout=10)
        after = time.monotonic() - began
        assert failure['reason'] == ('synthesis model call exceeded progress deadline: 1500 ms, the time the '
                                     'limits table gives it (model_call_ms)'), failure
        assert failure['resources_released'] is True
        assert stated - .2 <= after < 6, (
            f'the session failed {after:.2f} s after its synthesis began to hold; the table gives a model call {stated} s')
    finally:
        w.close()
