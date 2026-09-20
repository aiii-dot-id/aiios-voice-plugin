"""Shared wire shape only; native state tests own cross-event validation."""
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'spec/speaker_attribution.schema.json').read_text())
VECTORS = json.loads((ROOT / 'spec/speaker_attribution_vectors.json').read_text())['cases']
VALIDATOR = Draft202012Validator(SCHEMA)


def envelope(kind, sequence):
    return dict(type=kind, session_id='session-a', sequence=sequence,
                id=f'session-a:{sequence}', observed_monotonic_ns=123456)


def final(row):
    return dict(**envelope('transcript_final', row['final_sequence']),
                text='Fixture words', track_id=row['track_id'],
                start_sample=row['start_sample'], end_sample=row['end_sample'],
                attribution=copy.deepcopy(row['expected_initial']))


def amendment(row):
    return dict(**envelope('speaker_observation', row['final_sequence'] + 1),
                **copy.deepcopy(row['expected_amendment']))


def test_schema_is_valid():
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize('row', VECTORS, ids=lambda row: row['name'])
def test_shared_producer_shapes(row):
    VALIDATOR.validate(final(row))
    VALIDATOR.validate(amendment(row))


@pytest.mark.parametrize('damage', ['bare_final', 'permission', 'missing_span',
                                  'unknown_name', 'known_without_evidence',
                                  'fractional_ref', 'wrong_revision', 'pending_amendment',
                                  'known_unseparated', 'known_initial'])
def test_invalid_consumer_shape_refused(damage):
    item = amendment(VECTORS[0])
    if damage == 'bare_final':
        item = final(VECTORS[0]); del item['attribution']
    elif damage == 'permission': item['used_for_permissions'] = True
    elif damage == 'missing_span': del item['end_sample']
    elif damage == 'unknown_name': item['decision'] = 'unknown'
    elif damage == 'known_without_evidence': del item['evidence_scope']
    elif damage == 'fractional_ref': item['refers_to'] = 1.5
    elif damage == 'wrong_revision': item['revision'] = 0
    elif damage == 'pending_amendment': item['decision'] = 'pending'
    elif damage == 'known_unseparated': item['track_id'] = ''
    elif damage == 'known_initial':
        state = {k: item[k] for k in ('decision', 'reason', 'speaker', 'speaker_id',
                                      'revision', 'used_for_permissions', 'evidence_scope')}
        state['revision'] = 0
        item = final(VECTORS[0]); item['attribution'] = state
    with pytest.raises(ValidationError): VALIDATOR.validate(item)
