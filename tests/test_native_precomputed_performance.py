"""Startup gain cannot hide a decode, early-compute or finishing regression."""
import pytest

from scripts.audit_native_precomputed_windows import performance


def runs(candidate_ready=8, recognition=.9, partial=.9, finish=.9):
    values = []
    for arm in ('baseline', 'candidate', 'candidate', 'baseline'):
        candidate = arm == 'candidate'
        values.append({'arm': arm, 'ready': {'seconds': candidate_ready if candidate else 10},
                       'transcripts': [{'seconds': recognition if candidate else 1,
                                        'first_partial_unpaced_seconds': partial if candidate else 1,
                                        'finish_seconds': finish if candidate else 1}]})
    return values


def test_real_gain_and_no_compute_regression_earns_joint_gate_only():
    result = performance(runs())
    assert result['performance_gate_passed']
    assert result['ready_seconds_saved'] == 2


@pytest.mark.parametrize('kwargs', [{'candidate_ready': 9.5}, {'recognition': 1.06},
                                    {'partial': 1.06}, {'finish': 1.06}])
def test_startup_or_compute_failure_is_not_hidden(kwargs):
    assert not performance(runs(**kwargs))['performance_gate_passed']


def test_four_measurements_are_required():
    with pytest.raises(AssertionError): performance(runs()[:3])
