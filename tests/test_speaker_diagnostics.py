"""Diagnostics remain bounded, documented and independent of identity claims."""
import json
from pathlib import Path
import struct
import copy

import pytest

from jsonschema import Draft202012Validator

from scripts.audit_uid_conditions import conditions
from scripts.audit_uid_cross_utterance import decision, partition

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_profile_readiness_is_discoverable_without_biometric_data():
    schema = json.loads((ROOT / 'plugin/native/schemas/speaker-buckets.output.json').read_text())
    row = schema['properties']['speakers']['items']
    validator = Draft202012Validator(row)
    singleton = dict(speaker_uuid='00000000-0000-4000-8000-000000000001',
                     profile_available=True, matching_ready=False,
                     matching_reason='anonymous_profile_needs_corroboration',
                     evidence_sha256=['a'*64])
    validator.validate(singleton)
    validator.validate(dict(speaker_uuid=singleton['speaker_uuid'],
                            profile_available=True, matching_ready=True,
                            evidence_sha256=['a'*64, 'b'*64]))
    validator.validate(dict(speaker_uuid=singleton['speaker_uuid'],
                            profile_available=True, matching_ready=True,
                            enrollment_id='legacy-id', display_label='Context name'))
    assert 'two recordings' in json.loads((ROOT / 'plugin/native/schemas/speaker-buckets.input.json').read_text())['description']
    assert not {'embedding', 'audio', 'transcript'} & row['properties'].keys()
    assert list(validator.iter_errors({**singleton, 'matching_ready': 'true'}))


def test_management_readbacks_have_one_typed_shape():
    shapes = []
    for name in ('speaker.output.json', 'speaker-buckets.output.json'):
        schema = json.loads((ROOT / 'plugin/native/schemas' / name).read_text())
        Draft202012Validator.check_schema(schema)
        shapes.append(schema['properties']['speech'])
    assert shapes[0] == shapes[1]
    speech = shapes[0]
    assert set(speech['required']) == {'session_id', 'state_sequence', 'lifecycle',
                                      'input', 'synthesis', 'playback', 'attributions'}
    assert speech['properties']['attributions']['maxItems'] == 128
    diagnostic = speech['properties']['attributions']['items']['properties']['match']
    assert diagnostic['additionalProperties'] is False
    assert 'score' not in diagnostic['required']  # empty registry has no score
    assert 'margin' not in diagnostic['required']  # one candidate has no margin
    assert 'candidate_uuid' not in diagnostic['required']
    assert not {'embedding', 'audio', 'transcript'} & diagnostic['properties'].keys()


def test_condition_census_is_fixed_and_deterministic():
    pcm = struct.pack('<80000h', *([1000, -1000] * 40000))
    first = conditions(pcm)
    assert first == conditions(pcm)
    assert set(first) == {'gain_0_2', 'lowpass_5_samples', 'noise_20db',
                          'echo_80ms', 'first_2_seconds', 'last_2_seconds'}
    assert len(first['first_2_seconds']) == len(first['last_2_seconds']) == 64000
    assert all(len(value) == len(pcm) for name, value in first.items()
               if not name.endswith('2_seconds'))


def test_cross_utterance_decision_does_not_relax_policy():
    assert decision([.46, .1], .56, .105)[3] is False
    assert decision([.9, .85], .56, .105)[3] is False
    assert decision([.9, .9], .56, 0)[3] is False
    assert decision([.6, .4], .56, .105)[3] is True


def test_cross_utterance_partition_refuses_query_contamination():
    panel, extra = [], []
    for speaker in range(30):
        for i in range(8):
            row=dict(id=f'{speaker}-{i}', audio_sha256=f'hash-{speaker}-{i}',
                     speaker_id=speaker, chapter_id=0 if i in (0, 6, 7) else 1)
            (panel if i < 6 else extra).append(row)
    refs, queries=partition(panel, extra)
    assert len(refs) == 30 and len(queries) == 150
    contaminated=copy.deepcopy(panel)
    contaminated[1]['chapter_id']=0
    with pytest.raises(ValueError, match='chapters overlap'):
        partition(contaminated, extra)
    duplicate=copy.deepcopy(extra)
    duplicate[0]['audio_sha256']=panel[0]['audio_sha256']
    with pytest.raises(ValueError, match='Duplicate'):
        partition(panel, duplicate)


def test_attribution_diagnostics_reject_private_or_invented_metrics():
    schema=json.loads((ROOT/'spec/speaker_attribution.schema.json').read_text())['$defs']['match']
    validator=Draft202012Validator(schema)
    baseline=dict(outcome='unknown', reason='no_enrollments', candidate_count=0,
                  threshold=.56, minimum_margin=.105, profile_revision='0',
                  policy_sha256='a'*64, embedding_binding='b'*64, evidence_samples=64000)
    validator.validate(baseline)
    for key, value in [('score', 0), ('embedding', []), ('margin', 0)]:
        assert list(validator.iter_errors({**baseline, key:value}))
    populated={**baseline, 'candidate_count':1, 'candidate_uuid':'12345678-1234-4234-8234-123456789abc', 'score':.46}
    validator.validate(populated)
    assert list(validator.iter_errors({**populated, 'candidate_count':2}))
    validator.validate({**populated, 'candidate_count':2, 'margin':.2})
