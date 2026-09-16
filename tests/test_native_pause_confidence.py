import pytest

from scripts.screen_native_pause_confidence import boundaries, select


def test_query_and_commit_follow_source_not_wall_time():
    calls = []
    def score(start, end):
        calls.append((start, end)); return .8
    result = boundaries([1.]*10+[0.]*24, score, .5)
    assert calls == [(0, 30*512)]
    assert result['boundaries'] == [dict(start=0, end=34*512, reason='semantic_pause')]


def test_equality_holds_to_bounded_silence():
    result = boundaries([1.]+[0.]*60, lambda *_: .5, .5)
    assert result['boundaries'] == [dict(start=0, end=61*512, reason='bounded_silence')]
    assert len(result['queries']) == 1


def test_resume_retires_pending_without_commit_and_queries_again():
    result = boundaries([1.]+[0.]*22+[1.]+[0.]*24, lambda *_: .99, .5)
    assert len(result['queries']) == 2
    assert result['boundaries'] == [dict(start=0, end=48*512, reason='semantic_pause')]


def test_new_turn_preroll_is_exactly_uncommitted_tail():
    result = boundaries([1.]*10+[0.]*24+[1.]+[0.]*24, lambda *_: .8, .5)
    assert result['boundaries'][1]['start'] == 30*512
    assert result['queries'][1]['start'] == 30*512


def test_context_is_bounded_and_never_future_audio():
    result = boundaries([1.]*400+[0.]*24, lambda *_: .8, .5)
    assert result['queries'][0]['start'] == 170*512
    assert result['queries'][0]['end'] == 420*512


@pytest.mark.parametrize('p', [float('nan'), -1., 2.])
def test_invalid_model_score_is_failure(p):
    with pytest.raises(ValueError, match='invalid semantic'):
        boundaries([1.]+[0.]*24, lambda *_: p, .5)


def test_selection_refuses_delay_despite_better_accuracy():
    base = dict(cases=343, completions=81, missing=0, premature=100,
                premature_by_task=dict(candor_pause_handling=70, synthetic_pause_handling=30), median=.7, p90=1.)
    good = {**base, 'premature': 49, 'median': .8, 'p90': 1.2}
    slow = {**good, 'premature': 20, 'p90': 1.257}
    assert select({'0.01': base.copy(), '0.5': good.copy(), '0.9': slow.copy()}) == .5
    assert select({'0.01': base.copy(), '0.9': slow.copy()}) is None


def test_selection_refuses_a_failed_stratum_or_missing_completion():
    base = dict(cases=343, completions=81, missing=0, premature=100,
                premature_by_task=dict(candor_pause_handling=70, synthetic_pause_handling=30), median=.7, p90=1.)
    good = {**base, 'premature': 49}
    worse = {**good, 'premature_by_task': dict(candor_pause_handling=0, synthetic_pause_handling=49)}
    assert select({'0.01': base.copy(), '0.5': worse}) is None
    assert select({'0.01': base.copy(), '0.5': {**good, 'missing': 1}}) is None
