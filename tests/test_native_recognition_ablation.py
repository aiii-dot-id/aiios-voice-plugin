import pytest
from scripts.run_native_recognition_ablation import compare


def fixture():
    cases = [{'id':'one','native_task':'natural','samples':12000,'ablation_pcm':'/one.f32'}]
    rows = [{'type':'ready','provider':'CPUExecutionProvider'},
            {'type':'transcript','input':'/one.f32','samples':60000,'model_padding':10560,
             'exhausted':True,'text':'keep opening words'}]
    prior = {'recognition_records':{'one':{'reference':'keep opening words',
        'hypothesis':'opening words','word_errors':1,'reference_word_count':3}}}
    return cases, rows, prior


def test_accuracy_comparison_recomputes_both_sides():
    c,r,p = fixture(); result = compare(c,r,p)['summary']['natural']
    assert result['loop_errors'] == 1 and result['whole_errors'] == 0
    assert result['improved'] == 1 and result['worsened'] == 0


@pytest.mark.parametrize('mutation', ['missing','duplicate','wrong_input','lost_tail','not_final','wrong_padding','wrong_provider','invented_baseline'])
def test_incomplete_or_mismatched_ablation_cannot_pass(mutation):
    c,r,p = fixture()
    if mutation == 'missing': r.pop()
    if mutation == 'duplicate': r.append(dict(r[-1])); c.append(dict(c[-1]))
    if mutation == 'wrong_input': r[-1]['input'] = '/different.f32'
    if mutation == 'lost_tail': r[-1]['samples'] -= 1
    if mutation == 'not_final': r[-1]['exhausted'] = False
    if mutation == 'wrong_padding': r[-1]['model_padding'] = 0
    if mutation == 'wrong_provider': r[0]['provider'] = 'different'
    if mutation == 'invented_baseline': p['recognition_records']['one']['word_errors'] = 0
    with pytest.raises(ValueError): compare(c,r,p)


def test_unreferenced_audio_is_counted_but_not_scored():
    c,r,p = fixture(); p['recognition_records'] = {}
    result = compare(c,r,p)
    assert result['recordings'] == 1 and result['summary'] == {}


def test_regression_is_not_summarized_as_improvement():
    c,r,p = fixture();r[-1]['text'] = 'a completely different utterance'
    s = compare(c,r,p)['summary']['natural']
    assert s['worsened'] == 1 and s['improved'] == 0
