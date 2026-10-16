"""Signing evidence consumers must not promote partial or stale recorded runs."""
import copy
import json
from unittest.mock import patch
import pytest
from scripts.verify_separated_signing_input import sha, validate_resident_result, verify

def sample():
    cases = [dict(label='case-'+str(n), stable=True, finals=[dict(sequence=n, track_id='track', elapsed=1)],
                  observations=[dict(refers_to=n, track_id='track', elapsed=1.1)]) for n in range(28)]
    for n in range(3):
        # As recorded: a mixture is graded by attribution, not one stable UUID.
        cases[n].pop('stable')
        cases[n].update(label='mixture-'+str(n), interruption_recovery=n == 0,
                        attribution=dict(attribution_passed=True, text_passed=True))
    return dict(passed=True, installed=False, expanded_acquisition=True, identity_lifecycle=True,
                solo_acquisition_passed=True, identity_safety_passed=True, normal_recovery_passed=True,
                process_retired=True, retirement_exit=0, seconds=1, cases=cases,
                # A short quiet turn can abstain; later normal recovery is still required.
                solo_recovery_passed=False, complete_overlap_protocol=dict(gains=[[1,1],[1,.25],[.25,1]],
                max_speaker_wer=.35, max_case_wer=.25))

def test_complete_expanded_panel_allows_quiet_abstention():
    validate_resident_result(sample())

@pytest.mark.parametrize('key,value', [('passed', False), ('installed', True), ('failure', 'fault'),
    ('identity_safety_passed', False), ('normal_recovery_passed', False), ('process_retired', False),
    ('retirement_exit', True), ('retirement_exit', 1), ('seconds', float('nan'))])
def test_terminal_failure_refuses(key, value):
    row = sample(); row[key] = value
    with pytest.raises(ValueError): validate_resident_result(row)

@pytest.mark.parametrize('value', ['absent', None, False, 1, 'true'])
def test_case_stability_must_be_recorded_not_assumed(value):
    # A proof that omits stability has not shown a stable UUID; silence is not a pass.
    row = sample()
    if value == 'absent': row['cases'][5].pop('stable')
    else: row['cases'][5]['stable'] = value
    with pytest.raises(ValueError, match='unstable'): validate_resident_result(row)

@pytest.mark.parametrize('value', [None, False, 1, 'true'])
def test_a_stability_a_mixture_records_must_still_be_true(value):
    row = sample(); row['cases'][1]['stable'] = value
    with pytest.raises(ValueError, match='unstable'): validate_resident_result(row)

def test_a_recorded_stable_mixture_is_admitted():
    row = sample(); row['cases'][1]['stable'] = True
    validate_resident_result(row)

def test_changed_join_duplicate_case_and_overlap_failure_refuse():
    for mutation in ('join', 'duplicate', 'overlap', 'deadline'):
        row = sample()
        if mutation == 'join': row['cases'][0]['observations'][0]['track_id'] = 'other'
        if mutation == 'duplicate': row['cases'][-1] = copy.deepcopy(row['cases'][0])
        if mutation == 'overlap': row['cases'][0]['attribution']['text_passed'] = False
        if mutation == 'deadline': row['cases'][0]['observations'][0]['elapsed'] = 4.01
        with pytest.raises(ValueError): validate_resident_result(row)

def test_audit_must_bind_actual_proof_and_checkpoint(tmp_path):
    frozen = tmp_path/'freeze.json'; frozen.write_text('{}')
    original = tmp_path/'proof.json'
    row = sample(); row['bindings'] = {str(frozen):sha(frozen)}
    original.write_text(json.dumps(row))
    audit = dict(passed=True, installed=False, published=False, cases=28,
        checkpoint=str(tmp_path), proof=str(original), proof_sha256=sha(original),
        runtime_manifest_sha256='runtime', carrier_sha256='carrier', bindings=row['bindings'])
    evidence = tmp_path/'audit.json'
    def save():
        evidence.write_text(json.dumps(audit)); return sha(evidence)
    with patch('scripts.verify_separated_signing_input.verify_checkpoint', return_value=(
        dict(runtime_manifest_sha256='runtime', carrier_sha256='carrier'), {}, {}, row['bindings'])):
        assert verify(tmp_path, evidence, save())['installed'] is False
        original.write_text('{}')
        with pytest.raises(ValueError, match='original resident proof'): verify(tmp_path, evidence, save())
