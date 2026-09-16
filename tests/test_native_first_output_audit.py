import copy
import json

import pytest

from scripts.audit_native_first_output import phases, split_warm_trace, portable_inventory


def fixture():
    rows = []
    for i in range(6):
        base = i * 1000000000
        c = i * 10000000
        rows.append({'component': 'native-tts-phases', 'available': True, 'client': i % 3 + 1,
                     'generation': i + 1, 'begin_ns': base, 'start_ns': base + 1000000,
                     'first_ns': base + 10000000, 'retired_ns': base + 100000000,
                     'first_samples': 1920, 'start_result': 0,
                     'before': [c, c, c, i * 10], 'prepared': [c, c, c, i * 10],
                     'first': [c + 1000000, c + 2000000, c + 3000000, i * 10 + 1],
                     'retired': [c + 10000000, c + 10000000, c + 10000000, i * 10 + 10]})
    return rows


def test_disjoint_attribution():
    r = phases(fixture())
    assert len(r) == 4 and r[0]['native_first_pcm_ms'] == 10
    assert r[0]['stream_prepare_ms'] == 1 and r[0]['acoustic_ms'] == 2
    assert r[0]['decoder_ms'] == 3 and r[0]['other_native_ms'] == 4


@pytest.mark.parametrize('damage', ['missing', 'reset', 'time', 'negative', 'overlap', 'reuse', 'no_first', 'missing_reply'])
def test_corrupt_measurements_cannot_certify_attribution(damage):
    rows = copy.deepcopy(fixture())
    if damage == 'missing': rows[1]['available'] = False
    elif damage == 'reset': rows[1]['before'][0] = 0
    elif damage == 'time': rows[0]['first_ns'] = rows[0]['start_ns'] - 1
    elif damage == 'negative': rows[0]['before'][2] = -1
    elif damage == 'overlap': rows[0]['first'][0] = 9000000
    elif damage == 'reuse': rows[1]['generation'] = rows[0]['generation']
    elif damage == 'no_first': rows[0]['first_ns'] = rows[0]['first_samples'] = 0; rows[0]['first'] = [0] * 4
    elif damage == 'missing_reply': rows[2]['client'] = 2
    with pytest.raises(AssertionError): phases(rows)


@pytest.mark.parametrize('boundary', ['correct', 'missing', 'failed', 'repeated'])
def test_warmup_cannot_become_first_response(boundary):
    warm = fixture()[0]
    speech = fixture()[1]; speech['client'] = 1
    marker = {'component': 'native-startup-profile', 'phase': 'warm_all', 'completed': boundary != 'failed'}
    rows = [warm]
    if boundary != 'missing': rows.append(marker)
    if boundary == 'repeated': rows.append(marker)
    rows.append(speech)
    log = '\n'.join(json.dumps(r) for r in rows)
    if boundary == 'correct': assert split_warm_trace(log) == ([warm], [speech])
    else:
        with pytest.raises(AssertionError): split_warm_trace(log)


def test_windows_source_separator_is_not_byte_drift():
    assert portable_inventory({'runtime\\native\\a.cpp': 'digest'}) == {'runtime/native/a.cpp': 'digest'}
    with pytest.raises(AssertionError):
        portable_inventory({'runtime\\native\\a.cpp': 'digest', 'runtime/native/a.cpp': 'digest'})
