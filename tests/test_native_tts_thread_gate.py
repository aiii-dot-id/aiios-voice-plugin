"""The thread-budget gate must refuse a faster loop with worse startup."""
import copy

import pytest

from scripts.audit_native_tts_threads import performance


LIMITS = {'first_pcm_mean_ratio_max': .9,
          'first_pcm_mean_seconds_saved_min': .020,
          'total_compute_ratio_max': 1.05, 'load_ratio_max': 1.05}


def runs(load=3., first=.3, compute=2.7):
    values = []
    for threads in (4, 1, 1, 4):
        values.append({'threads': threads, 'rows': [
            {'type': 'ready', 'seconds': 3. if threads == 4 else load},
            *[{'type': 'synthesis', 'first_pcm_seconds': .4 if threads == 4 else first,
               'seconds': 3. if threads == 4 else compute, 'samples': 144000}
              for _ in range(3)]]})
    return values


def test_confirmed_component_gain_passes():
    result = performance(runs(), LIMITS)
    assert result['candidates'][1]['component_performance_gate_passed']
    assert result['candidates'][1]['candidate_over_baseline']['first_pcm_seconds'] == pytest.approx(.75)


@pytest.mark.parametrize('kwargs', [dict(load=3.16), dict(first=.38), dict(compute=3.16)])
def test_each_performance_regression_is_refused(kwargs):
    assert not performance(runs(**kwargs), LIMITS)['candidates'][1]['component_performance_gate_passed']


def test_fractional_gain_cannot_hide_tiny_absolute_saving():
    evidence = runs()
    for run in evidence:
        for row in run['rows'][1:]:
            row['first_pcm_seconds'] = .010 if run['threads'] == 4 else .001
    assert not performance(evidence, LIMITS)['candidates'][1]['component_performance_gate_passed']


def test_missing_replicate_is_not_a_comparison():
    with pytest.raises(AssertionError):
        performance(copy.deepcopy(runs()[:-1]), LIMITS)
