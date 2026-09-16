import ast
import copy
from pathlib import Path
import zipfile

import pytest

from scripts.windows_cold_speech import ColdStartCall, wait_cold
from scripts.audit_native_cold_session import verify_cold, timing, verify_images
from scripts.stage_native_cold_session import PARENT, ORDER, child_program, owner_program


COLD = dict(pstate=8, sm_mhz=139, memory_mhz=405, gpu_percent=0, temperature_c=45)


class Clock:
    def __init__(self):self.t = 100
    def __call__(self):return self.t
    def sleep(self, seconds):self.t += seconds


class FakeGPU:
    identity = {'name': 'test GPU'}
    def __init__(self, clock, values=None, delay=.01, close_delay=0):
        self.clock, self.values, self.delay, self.close_delay = clock, iter(values or [COLD] * 10), delay, close_delay
        self.closed = False
    def snapshot(self):
        self.clock.sleep(self.delay)
        return next(self.values)
    def close(self):
        self.closed = True
        self.clock.sleep(self.close_delay)


def test_cold_requires_two_consecutive_samples_and_resets_after_a_warm_sample():
    c = Clock();g = FakeGPU(c, [COLD, dict(COLD, sm_mhz=1556), COLD, COLD])
    r = wait_cold(g, 99, clock=c, sleep=c.sleep)
    assert len(r['rows']) == 4
    assert r['end_elapsed'] == pytest.approx(1.79)
    assert all(b['elapsed'] - a['elapsed'] >= .25 for a, b in zip(r['rows'], r['rows'][1:]))


@pytest.mark.parametrize('delay', [.01, 31])
def test_deadline_includes_a_slow_native_observation(delay):
    c = Clock();g = FakeGPU(c, [dict(COLD, pstate=0)] * 200, delay=delay)
    with pytest.raises(TimeoutError):wait_cold(g, 99, clock=c, sleep=c.sleep)


def test_control_arguments_unchanged_and_observation_only_precedes_first_synthesis():
    c = Clock();g = FakeGPU(c);calls = []
    def delegate(*args, **kwargs):
        calls.append((args, kwargs));return 'accepted'
    gate = ColdStartCall(delegate, 99, factory=lambda:g, clock=c, sleep=c.sleep)
    assert gate('open', {'session_id': 's'}, timeout=5) == 'accepted'
    assert gate.record is None and not g.closed
    args = {'session_id': 's', 'synthesis_id': '1', 'text': 'unchanged'}
    assert gate('synthesize', args, 10) == 'accepted'
    assert g.closed and gate.record['session_id'] == 's' and gate.record['synthesis_id'] == '1'
    first = dict(gate.record)
    gate('stop_playback', {'session_id': 's'})
    gate('synthesize', dict(args, synthesis_id='2'))
    assert gate.record == first and len(calls) == 4
    assert calls[1] == (('synthesize', args, 10), {}) and calls[1][0][1] is args


@pytest.mark.parametrize('failure', ['stale', 'timeout', 'query'])
def test_failed_cold_check_closes_observer_and_never_submits_or_retries(failure):
    c = Clock();g = FakeGPU(c, close_delay=.2 if failure == 'stale' else 0,
                           delay=31 if failure == 'timeout' else .01)
    if failure == 'query':
        def broken():raise OSError('GPU query failed')
        g.snapshot = broken
    calls = [];gate = ColdStartCall(lambda *a, **kw: calls.append(a), 99, factory=lambda:g, clock=c, sleep=c.sleep)
    with pytest.raises((RuntimeError, TimeoutError, OSError)):
        gate('synthesize', {'session_id': 's', 'synthesis_id': '1'})
    assert g.closed and not calls and 'error' in gate.record and not gate.admitted
    assert gate.record['identity'] == g.identity
    assert gate.record['rows'] or failure == 'query'
    with pytest.raises(RuntimeError, match='previous cold admission failed'):
        gate('synthesize', {'session_id': 's', 'synthesis_id': '2'})
    assert not calls


def test_every_failed_observation_is_retained_not_just_the_timeout_message():
    c = Clock();g = FakeGPU(c, [dict(COLD, pstate=0, sm_mhz=1556)] * 200)
    trace = {}
    with pytest.raises(TimeoutError):wait_cold(g, 99, clock=c, sleep=c.sleep, trace=trace)
    assert not trace['condition_passed'] and 'TimeoutError' in trace['error']
    assert len(trace['rows']) > 100
    assert all(x['gpu']['pstate'] == 0 and x['gpu']['sm_mhz'] == 1556 for x in trace['rows'])
    assert trace['end_elapsed'] - trace['begin_elapsed'] < 30


def record():
    return dict(begin_elapsed=1, end_elapsed=1.3, submitted_elapsed=1.31, session_id='s', synthesis_id='1',
                rows=[dict(elapsed=t, read_seconds=.01, gpu=dict(COLD)) for t in (1.01, 1.3)])


def first():return dict(elapsed=1.32, session_id='s', synthesis_id='1')


def test_independent_observation_accepts_exact_condition_at_submission():
    verify_cold(record(), first())


@pytest.mark.parametrize('damage', ['stale-submit', 'stale-start', 'session', 'synthesis', 'one', 'warm',
                                  'busy', 'hot', 'clock', 'nan', 'spacing', 'negative-read', 'overlap'])
def test_independent_observation_refuses_false_cold_or_wrong_stream(damage):
    r, f = record(), first()
    if damage == 'stale-submit':r['submitted_elapsed'] += .2
    elif damage == 'stale-start':f['elapsed'] += .3
    elif damage == 'session':f['session_id'] = 'another'
    elif damage == 'synthesis':f['synthesis_id'] = 'another'
    elif damage == 'one':r['rows'].pop(0)
    elif damage == 'warm':r['rows'][0]['gpu']['sm_mhz'] = 1556
    elif damage == 'busy':r['rows'][-1]['gpu']['gpu_percent'] = 2
    elif damage == 'hot':r['rows'][-1]['gpu']['temperature_c'] = 61
    elif damage == 'clock':r['rows'][-1]['gpu']['memory_mhz'] = 4000
    elif damage == 'nan':r['rows'][0]['elapsed'] = float('nan')
    elif damage == 'spacing':r['rows'][0]['elapsed'] += .1
    elif damage == 'negative-read':r['rows'][0]['read_seconds'] = -.1
    else:r['rows'][1]['read_seconds'] = .4
    with pytest.raises(AssertionError):verify_cold(r, f)


def runs():
    return [dict(arm=arm, speech=[dict(first_pcm_ms=400, rtf=.5) for _ in range(4)],
                 readiness=dict(host_startup_timing=dict(spawn_to_ready_seconds=40 if arm == 'baseline' else 25)))
            for arm in ORDER]


def test_independent_eight_run_timing_gate_accepts_preserved_speech_and_saved_startup():
    assert timing(runs())['performance_gate_passed']


@pytest.mark.parametrize('damage', ['first-pcm', 'rtf-position', 'individual-rtf', 'startup'])
def test_a_single_failed_timing_condition_is_not_diluted_into_green(damage):
    r = runs()
    if damage == 'first-pcm':r[1]['speech'][2]['first_pcm_ms'] = 481
    elif damage == 'rtf-position':r[1]['speech'][2]['rtf'] = .601
    elif damage == 'individual-rtf':
        for row in r:row['speech'][2]['rtf'] = .99
        r[1]['speech'][2]['rtf'] = 1.001
    else:
        for row in r:
            if row['arm'] == 'candidate':row['readiness']['host_startup_timing']['spawn_to_ready_seconds'] = 37
    assert not timing(r)['performance_gate_passed']


@pytest.mark.parametrize('damage', ['short', 'order', 'fewer-replies', 'nan', 'negative'])
def test_missing_or_invalid_timing_evidence_is_refused(damage):
    r = runs()
    if damage == 'short':r.pop()
    elif damage == 'order':r[0]['arm'] = 'candidate'
    elif damage == 'fewer-replies':r[0]['speech'].pop()
    elif damage == 'nan':r[1]['speech'][0]['first_pcm_ms'] = float('nan')
    else:r[1]['speech'][0]['rtf'] = -1
    with pytest.raises(AssertionError):timing(r)


def test_child_keeps_original_sdk_calls_and_owner_has_exact_order_no_sampler():
    with zipfile.ZipFile(PARENT) as z:raw = z.read('scripts/compare_native_composition_tts_windows.py')
    child = child_program(raw)
    def calls(raw):
        tree = ast.parse(raw)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'child')
        names = {'SDKHost', 'run', 'observe', 'speech_metrics', 'host.close', 'host.readiness'}
        return [ast.dump(n, include_attributes=False) for n in ast.walk(fn)
                if isinstance(n, ast.Call) and ast.unparse(n.func) in names]
    assert calls(raw) == calls(child)
    assert b'CPUSampler' not in child and b'time.sleep(' not in child
    root = Path(__file__).resolve().parents[1]
    owner = owner_program((root / 'scripts/profile_native_session_resources_windows.py').read_bytes())
    assert b"['baseline','candidate','candidate','baseline']*2" in owner
    assert b'resource_session_child' not in owner and b'resource-contract' not in owner
    assert b"row['gpu_cold']['identity']" in owner


@pytest.mark.parametrize('damage', [None, 'extended-path', 'empty', 'missing', 'path', 'hash', 'native-map', 'worker-path'])
def test_loaded_evidence_requires_every_exact_native_image(damage):
    libraries = {'asr.dll': 'a' * 64, 'tts.dll': 'b' * 64}
    images = {n: dict(path='C:\\proof\\' + n, sha256=h) for n, h in libraries.items()}
    worker = dict(bound_images=copy.deepcopy(images), native_images=copy.deepcopy(images))
    worker['native_images']['aii_voice_worker.exe'] = dict(path='C:\\proof\\aii_voice_worker.exe', sha256='c' * 64)
    profile = dict(libraries=libraries, worker='C:/proof/aii_voice_worker.exe')
    bindings = {profile['worker']: 'c' * 64}
    if damage == 'extended-path':
        worker['native_images']['aii_voice_worker.exe']['path'] = '\\\\?\\C:\\proof\\aii_voice_worker.exe'
    elif damage == 'empty':worker['bound_images'] = {}
    elif damage == 'missing':worker['bound_images'].pop('asr.dll')
    elif damage == 'path':worker['bound_images']['asr.dll']['path'] = 'C:/elsewhere/asr.dll'
    elif damage == 'hash':worker['bound_images']['asr.dll']['sha256'] = 'd' * 64
    elif damage == 'native-map':worker['native_images']['asr.dll']['sha256'] = 'd' * 64
    elif damage == 'worker-path':worker['native_images']['aii_voice_worker.exe']['path'] = 'C:/elsewhere/aii_voice_worker.exe'
    if damage and damage != 'extended-path':
        with pytest.raises(AssertionError):verify_images(worker, profile, bindings)
    else:verify_images(worker, profile, bindings)
