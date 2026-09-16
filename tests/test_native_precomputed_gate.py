"""The Windows gate must reject changed words, trajectories and missing recovery."""
import copy

import pytest

from scripts.compare_native_precomputed_windows import check_rows


def fixture_rows():
    target = {'text': 'opening words', 'tokens': [1, 2], 'trajectory': [[56, 'opening words']]}
    short = {'type': 'transcript', **target, 'model_padding': 10560}
    long = {**short, 'samples': 1059840, 'exhausted': True, 'retained_peak_samples': 12000}
    rows = [{'type': 'ready'}, short, long, copy.deepcopy(long),
            {'type': 'cancel', 'inference_entered': True, 'result_suppressed': True}, copy.deepcopy(short)]
    return rows, {'recovery': target}, target


def test_frozen_gate_accepts_matching_rows():
    rows, expected, long = fixture_rows()
    assert check_rows(rows, expected, long)['cancel']['result_suppressed']


@pytest.mark.parametrize('damage', ['text', 'tokens', 'trajectory', 'tail', 'retained', 'cancel', 'recovery-order', 'missing-recovery'])
def test_frozen_gate_rejects_specific_harm(damage):
    rows, expected, long = fixture_rows()
    if damage == 'text': rows[-1]['text'] = 'words'
    elif damage == 'tokens': rows[1]['tokens'] = [2]
    elif damage == 'trajectory': rows[1]['trajectory'] = [[112, 'opening words']]
    elif damage == 'tail': rows[2]['model_padding'] = 0
    elif damage == 'retained': rows[2]['retained_peak_samples'] = 1059840
    elif damage == 'cancel': rows[-2]['result_suppressed'] = False
    elif damage == 'recovery-order': rows[-2], rows[-1] = rows[-1], rows[-2]
    elif damage == 'missing-recovery': rows.pop()
    with pytest.raises(AssertionError): check_rows(rows, expected, long)
