from copy import deepcopy
import pytest
from scripts.score_identity_attribution import evaluate


def fixture():
    refs = {'a': 'silver lantern beside the gate', 'b': 'copper kettle above the stove'}
    finals = [dict(session_id='s', sequence=i+1, track_id=str(i), text=t) for i, t in enumerate(refs.values())]
    observations = [dict(session_id='s', refers_to=i+1, track_id=str(i), speaker_uuid=u, reason='matched')
                    for i, u in enumerate(['uuid-a', 'uuid-b'])]
    return refs, finals, observations, {'a': 'uuid-a', 'b': 'uuid-b'}


def grade(data):
    return evaluate(*data, max_speaker_wer=.2)


def test_swapped_uuids_fail_even_when_set_is_correct():
    data = fixture()
    assert grade(data)['attribution_passed']
    data[2][0]['speaker_uuid'], data[2][1]['speaker_uuid'] = 'uuid-b', 'uuid-a'
    assert not grade(data)['attribution_passed']


def test_track_order_does_not_determine_identity():
    data = fixture()
    data[1].reverse()
    data[2].reverse()
    assert grade(data)['attribution_passed']


def test_abstention_is_not_identity_failure_or_coverage():
    data = fixture()
    del data[2][1]['speaker_uuid']
    result = grade(data)
    assert result['attribution_passed'] and result['resolved'] == result['abstained'] == 1
    data[2][1]['speaker_uuid'] = 'new-duplicate'
    assert not grade(data)['attribution_passed']


def test_unknown_cannot_inherit_known_uuid():
    data = fixture()
    data[3]['b'] = None
    del data[2][1]['speaker_uuid']
    assert grade(data)['attribution_passed']
    data[2][1]['speaker_uuid'] = 'uuid-a'
    assert not grade(data)['attribution_passed']


@pytest.mark.parametrize('defect', ['missing', 'duplicate', 'session', 'track', 'sequence'])
def test_exact_join_required(defect):
    data = fixture()
    if defect == 'missing': data[2].pop()
    elif defect == 'duplicate': data[2].append(deepcopy(data[2][0]))
    elif defect == 'session': data[2][0]['session_id'] = 'old'
    elif defect == 'track': data[2][0]['track_id'] = 'other'
    else: data[2][0]['refers_to'] = 42
    with pytest.raises(ValueError): grade(data)


def test_identical_reference_text_cannot_prove_identity_assignment():
    data = fixture()
    data[0]['b'] = data[0]['a']
    assert grade(data)['reason'] == 'ambiguous_text_assignment'


def test_pooled_words_and_missing_track_fail():
    data = fixture()
    data[1][0]['text'] += ' ' + data[1][1]['text']
    assert not grade(data)['attribution_passed']
    data[1].pop(); data[2].pop()
    assert grade(data)['reason'] == 'missing_or_extra_track'


def test_short_foreign_phrase_does_not_hide_inside_allowed_wer():
    data = fixture()
    data[0]['a'] += ' glowing softly in the cold evening air'
    data[1][0]['text'] = data[0]['a'] + ' copper kettle'
    result = evaluate(*data, max_speaker_wer=.35)
    assert result['text_passed'] and not result['attribution_passed']


def test_all_abstentions_do_not_claim_resolved_coverage():
    data = fixture()
    for observation in data[2]: del observation['speaker_uuid']
    result = grade(data)
    assert result['attribution_passed'] and result['resolved'] == 0 and result['abstained'] == 2


def test_bad_unattributed_text_fails_text_not_identity_safety():
    data = fixture()
    del data[2][1]['speaker_uuid']
    data[1][1]['text'] = 'copper kettle'
    result = grade(data)
    assert result['attribution_passed'] and not result['text_passed']
    data[2][1]['speaker_uuid'] = 'uuid-b'
    assert not grade(data)['attribution_passed']
