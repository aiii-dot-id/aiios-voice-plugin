import numpy as np
import pytest

from scripts.prove_separator_uid import identify, metrics, si_sdr, unit


def test_unknowns_and_wrong_matches_are_not_counted_as_success():
    rows = [
        {'truth': 'a', 'known': True, 'decision': {'speaker': 'a'}},
        {'truth': 'b', 'known': True, 'decision': {'speaker': 'a'}},
        {'truth': 'c', 'known': True, 'decision': {'speaker': None}},
        {'truth': 'guest', 'known': False, 'decision': {'speaker': 'a'}},
        {'truth': 'other', 'known': False, 'decision': {'speaker': None}},
    ]
    assert metrics(rows) == {'known': 3, 'correct': 1, 'wrong': 2, 'unknown': 2}


def test_ties_threshold_and_margin_are_not_relaxed():
    a, b = unit([1., 0.]), unit([0., 1.])
    assert identify(a, {'a': a, 'b': b}, .4, .14)['speaker'] == 'a'
    assert identify(unit([1., 1.]), {'a': a, 'b': b}, 0., 0.)['speaker'] is None
    assert identify(unit([1., 1.1]), {'a': a, 'b': b}, .4, .14)['speaker'] is None
    assert identify(-a, {'a': a, 'b': b}, .4, .14)['speaker'] is None


def test_waveform_similarity_alone_can_hide_destructive_conversion():
    x = np.sin(np.arange(72000, dtype=np.float32)*.17)*.2
    estimate = x*75
    assert si_sdr(estimate, x) > 80
    clipped = np.clip(estimate, -1., 1.)
    assert si_sdr(clipped, x) < 10


@pytest.mark.parametrize('bad', [[0., 0.], [np.inf, 1.], [np.nan, 1.]])
def test_invalid_embeddings_do_not_produce_scores(bad):
    with pytest.raises(ValueError):
        unit(bad)
