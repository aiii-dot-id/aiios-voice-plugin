from pathlib import Path

import pytest

from scripts.native_retirement_trace import REPLACEMENTS, classify, observe

ROOT = Path(__file__).resolve().parents[1]


def test_trace_reverses_to_exact_original_and_preserves_deadlines():
    original = (ROOT/'plugin/native/main.go').read_bytes()
    candidate = observe(original)
    text = candidate.decode()
    for old, new in reversed(REPLACEMENTS):
        text = text.replace(new, old, 1)
    assert text.encode() == original
    assert candidate.count(b'time.After(5 * time.Second)') == original.count(b'time.After(5 * time.Second)')
    assert candidate.count(b'killWorker(cmd)') == original.count(b'killWorker(cmd)')
    assert candidate.count(b'cmd.Wait()') == original.count(b'cmd.Wait()')


@pytest.mark.parametrize('action', ['remove', 'duplicate'])
def test_changed_seam_refused(action):
    source = (ROOT/'plugin/native/main.go').read_bytes()
    old = REPLACEMENTS[0][0].encode()
    changed = source.replace(old, b'' if action == 'remove' else old+old)
    with pytest.raises(ValueError):
        observe(changed)


@pytest.mark.parametrize('tail, expected', [
    ([], 'cleanup_incomplete_without_reap_verdict'),
    (['worker-reap-deadline'], 'worker_output_not_closed_before_reap_deadline'),
    (['worker-output-closed', 'worker-reap-deadline'], 'output_closed_but_process_reap_missed_deadline'),
    (['worker-output-closed', 'worker-wait-returned', 'carrier-cleanup-end'], 'carrier_cleanup_returned'),
])
def test_trace_distinguishes_output_closure_from_process_exit(tail, expected):
    rows = [dict(component='voice-carrier-startup', phase='retirement-'+name,
                 elapsed_ms=1000+i*10)
            for i, name in enumerate(['carrier-cleanup-begin', *tail])]
    assert classify(rows)['boundary'] == expected
