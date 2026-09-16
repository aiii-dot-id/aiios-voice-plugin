import pytest
from scripts.screen_native_pause_recheck import boundaries, advance
from scripts.stage_native_pause_recheck import derive


def test_second_query_owns_fresh_audio_and_full_provisional_interval():
    calls=[]
    def score(s,e):
        calls.append((s,e));return .5 if len(calls)==1 else .8
    r=boundaries([1.]+[0.]*32,score)
    assert calls==[(0,21*512),(0,29*512)]
    assert r['boundaries']==[dict(start=0,end=33*512,reason='semantic_pause')]


def test_five_queries_then_fixed_silence_bound():
    r=boundaries([1.]+[0.]*60,lambda *_: 0.)
    assert [q['end'] for q in r['queries']]==[x*512 for x in (21,29,37,45,53)]
    assert r['boundaries']==[dict(start=0,end=61*512,reason='bounded_silence')]


def test_resumed_speech_wins_at_recheck_resolution():
    n=0
    def score(*_):
        nonlocal n
        n+=1;return 0. if n==1 else .9
    r=boundaries([1.]+[0.]*31+[1.]+[0.]*24,score)
    assert r['boundaries']==[dict(start=0,end=57*512,reason='semantic_pause')]
    assert len(r['queries'])==3


@pytest.mark.parametrize('value',[float('nan'),-1,2])
def test_bad_recheck_probability_refused(value):
    with pytest.raises(ValueError,match='invalid semantic'):
        boundaries([1.]+[0.]*20,lambda *_:value)


def test_unchanged_gate_refuses_one_missing_completion():
    base=dict(cases=343,completions=81,premature=104,median=.502,p90=1.5964,premature_by_task={'natural':96,'synthetic':8})
    improved={**base,'premature':20,'missing':1,'median':.6,'p90':1.6}
    assert not advance(base,improved)


def test_candidate_derivation_refuses_unknown_source():
    with pytest.raises(ValueError,match='source seam changed'):
        derive('unrelated source')
