import ast
import copy
import json
import zipfile

import pytest

from scripts.audit_native_session_resources import region
from scripts.stage_native_resource_session import PARENT, child_program


def rows():
    return [{'elapsed': n / 10, 'read_seconds': .001,
        'system_kernel_seconds': n * .4, 'system_user_seconds': n * .4, 'system_idle_seconds': n * .5,
        'processes': {label: {'pid': i + 10, 'creation_seconds': 99, 'kernel_seconds': n * .02,
            'user_seconds': n * .08, 'exited': False,
            'memory': {'working_bytes': 100, 'private_bytes': 200, 'peak_working_bytes': 100, 'page_faults': 3 * n}}
            for i, label in enumerate(('worker', 'carrier', 'observer'))},
        'gpu': {'sm_mhz': 1500, 'memory_mhz': 4000, 'gpu_percent': 50, 'memory_percent': 10,
            'memory_total': 8000, 'memory_used': 1000, 'memory_free': 7000, 'temperature_c': 45, 'pstate': 0},
        'system_memory': {'available_physical_bytes': 12000, 'total_physical_bytes': 32000,
                          'available_commit_bytes': 16000, 'total_commit_bytes': 64000}}
        for n in range(5)]


def test_actual_counter_deltas_not_cpu_percent_or_allocated_gpu_memory():
    r = region(rows(), 0, .4)
    assert r['cpu']['mean_cores'] == pytest.approx({'worker': 1, 'carrier': 1, 'observer': 1, 'system_busy': 3})
    assert r['page_faults']['worker'] == 12
    assert r['gpu']['gpu_percent']['mean'] == 50
    assert r['gpu']['memory_used']['mean'] == 1000
    assert r['observer_read_wall_fraction'] == pytest.approx(.0125)


@pytest.mark.parametrize('damage', ['pid', 'cpu', 'exit', 'faults', 'gpu', 'nan', 'memory', 'system-memory', 'short'])
def test_false_resource_evidence_is_not_zero_load(damage):
    r = copy.deepcopy(rows())
    if damage == 'pid':r[2]['processes']['worker']['pid'] += 1
    elif damage == 'cpu':r[2]['processes']['worker']['user_seconds'] = -1
    elif damage == 'exit':r[2]['processes']['worker']['exited'] = True
    elif damage == 'faults':r[2]['processes']['worker']['memory']['page_faults'] = 0
    elif damage == 'gpu':r[2]['gpu']['gpu_percent'] = 101
    elif damage == 'nan':r[2]['gpu']['sm_mhz'] = float('nan')
    elif damage == 'memory':r[2]['processes']['worker']['memory']['working_bytes'] = 500
    elif damage == 'system-memory':r[2]['system_memory']['available_physical_bytes'] = -1
    else:r = r[:1]
    with pytest.raises((AssertionError, ValueError)):region(r, 0, .4)


def test_instrumentation_preserves_all_sdk_calls_and_production_arguments():
    with zipfile.ZipFile(PARENT) as z:original = z.read('scripts/compare_native_composition_tts_windows.py')
    instrumented = child_program(original)
    def calls(raw):
        tree = ast.parse(raw)
        child = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'child')
        names = {'SDKHost', 'run', 'observe', 'speech_metrics', 'host.close', 'host.readiness'}
        return [ast.dump(n, include_attributes=False) for n in ast.walk(child)
                if isinstance(n, ast.Call) and ast.unparse(n.func) in names]
    assert calls(original) == calls(instrumented)
    text = instrumented.decode()
    assert "period=.05" in text and 'time.sleep(3)' in text and 'time.sleep(2)' in text
    assert text.index('sampler.close()') < text.index("result['exit_code'] = host.close()")
    assert 'if sampler.thread.ident is None:' in text
    compile(text, 'resource_child', 'exec')
