from scripts.prove_uid_gallery_readiness import tally, passed


def test_gate_requires_positive_and_negative_queries_and_no_wrong_matches():
    known = tally([dict(speaker_id='reference', outcome='known')], 'reference')
    absent = tally([dict(outcome='unavailable')], 'reference')
    assert passed(dict(positive=known, negative=absent))
    for answer in (dict(outcome='known', speaker_id='reference'),
                   dict(outcome='known', speaker_id='other'),
                   dict(outcome='unknown', speaker_id='reference'),
                   dict(outcome='known'),
                   dict(outcome='unavailable', speaker_uuid='anonymous')):
        assert not passed(dict(positive=known, negative=tally([answer], 'reference')))
    for answer in (dict(outcome='unknown', speaker_id='reference'),
                   dict(outcome='unavailable'), dict(outcome='known', speaker_id='other')):
        assert not passed(dict(positive=tally([answer], 'reference'), negative=absent))
    assert not passed(dict(positive=tally([], 'reference'), negative=absent))
    assert not passed(dict(positive=known, negative=tally([], 'reference')))
