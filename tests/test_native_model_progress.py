"""Production dispatcher/core deadlines with model doubles, not installed qualification."""
import os
import queue
import time
from pathlib import Path

from scripts.prove_native_worker_transport import Worker


def open_output(w, sid):
    w.call('open', session_id=sid, output_handle='playback', audio={
        'format': 's16le', 'input': None, 'output': {'rate': 24000, 'channels': 1}})
    w.configure(w.settings.get(timeout=2), 768)
    w.event('session_ready', sid)


def test_stalled_models_fault_and_retire_before_recovery(tmp_path):
    binary = Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE'])
    # Start both independent children together; neither touches real models,
    # the GPU, devices, or an installed identity. Use the production 30s bound.
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
        # Status traffic stays responsive but cannot renew a model-call lease.
        while time.monotonic() - started < 40:
            if stubborn.p.poll() is not None:
                break
            for label, w in [('cooperative', cooperative), ('stubborn', stubborn)]:
                if w.p.poll() is None:
                    try:
                        state = w.status(label)
                    except (BrokenPipeError, OSError, queue.Empty):
                        assert w is stubborn and w.p.poll() == 72  # exit can race the final poll
                        break
                    assert state['lifecycle'] in ('open', 'draining', 'failed')
            time.sleep(.2)
        assert stubborn.p.poll() == 72, 'unretired inference must end the process nonzero'
        assert time.monotonic() - started >= 29, 'failed before the production call deadline'
        failure = cooperative.event('failure', 'cooperative')
        assert failure['reason'] == 'synthesis model call exceeded progress deadline'
        assert failure['resources_released'] is True
        assert not stubborn.frames and not cooperative.frames
        assert not any(e['type'] == 'session_end' for e in stubborn.events)
        assert not any(e.get('resources_released') for e in stubborn.events)
        # Successful cancellation permits a new session in the same worker.
        open_output(cooperative, 'recovery')
        result, _ = cooperative.call('synthesize', session_id='recovery', synthesis_id='fresh', text='Recovery.')
        cooperative.event('synthesis_end', 'recovery')
        due = time.monotonic() + 5
        stream = result['output_stream']
        while not any(f['stream'] == stream and f['kind'] == 3 for f in cooperative.frames):
            assert time.monotonic() < due
            time.sleep(.002)
        count = sum(f['samples'] for f in cooperative.frames if f['stream'] == stream)
        assert count == 960
        cooperative.call('playback_report', session_id='recovery', synthesis_id='fresh',
                         output_stream=stream, rendered_samples=count, terminal=True)
        cooperative.call('close', session_id='recovery', mode='drain')
        assert cooperative.event('session_end', 'recovery')['status'] == 'completed'
    finally:
        for index, w in enumerate(workers):
            # A failed assertion must never leave our deliberately stalled child.
            if w.p.poll() is None and index == 1:
                w.p.kill()
            w.close()
