import copy
import json

import pytest

from scripts.assemble_guided_beta_candidate import HEARING_REVIEW_ITEM, apply_hearing_disposition, sha


def fixture(tmp_path):
    models = {'stt/encoder': {'sha256': 'model', 'bytes': 12}}
    files = {'NOTICE': {'sha256': 'notice', 'bytes': 24}}
    index = dict(open_items=['unrelated', HEARING_REVIEW_ITEM], distribution_review_complete=False,
                 hearing_replacement=dict(models=models, files=files))
    cfg = dict(models=[dict(path='stt/encoder', sha256='model', size=12)])
    notices = [dict(path='notices/native-multitalker/NOTICE', sha256='notice', size=24),
               dict(path='notices/native-multitalker/HEARING-REPLACEMENT.json', sha256='record', size=50)]
    review = dict(engineering_distribution_review='bound_hearing_terms_reviewed',
                  resolved_item=HEARING_REVIEW_ITEM, models=models, record_sha256='record',
                  notices={'notices/native-multitalker/NOTICE': files['NOTICE']},
                  reasoning=['reviewed'], limitations=['not authentication'])
    source = tmp_path / 'review.json'
    source.write_text(json.dumps(review))
    return index, cfg, notices, source


def test_resolves_only_bound_hearing_question(tmp_path):
    index, cfg, notices, source = fixture(tmp_path)
    apply_hearing_disposition(index, cfg, notices, source, sha(source))
    assert index['open_items'] == ['unrelated']
    assert index['distribution_review_complete'] is False


@pytest.mark.parametrize('damage', ['digest', 'model', 'notice', 'record', 'missing', 'duplicate', 'review'])
def test_changed_review_or_bytes_fail_atomically(tmp_path, damage):
    index, cfg, notices, source = fixture(tmp_path)
    digest = sha(source)
    if damage == 'digest': digest = 'wrong'
    if damage == 'model': cfg['models'][0]['sha256'] = 'changed'
    if damage == 'notice': notices[0]['size'] += 1
    if damage == 'record': notices[1]['sha256'] = 'changed'
    if damage == 'missing': index['open_items'].remove(HEARING_REVIEW_ITEM)
    if damage == 'duplicate': index['open_items'].append(HEARING_REVIEW_ITEM)
    if damage == 'review':
        review = json.loads(source.read_text()); review['limitations'] = []
        source.write_text(json.dumps(review)); digest = sha(source)
    before = copy.deepcopy(index)
    with pytest.raises(ValueError):
        apply_hearing_disposition(index, cfg, notices, source, digest)
    assert index == before
