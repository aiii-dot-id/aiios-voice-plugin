"""Signing admission rejects incomplete proof; fixtures do not qualify models."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.verify_separated_signing_input import CASES, sha, validate_result, verify


def sample():
    def receipt(outcome):
        return dict(result=dict(accepted=True), observation=dict(
            terminal=True, outcome=outcome, delivered_samples=240))
    return dict(passed=True, process_retired=True, exit_code=0,
        registry_broker_retired=True, elapsed_seconds=1, installed=False,
        persistent_uid_qualified=False, broker_errors=[],
        registry=dict(resident_uuid_path=True, post_session_naming=True,
            process_restart_qualified=True, confirmed_forget_after_close=True,
            test_host_only=True, acoustic_match_after_restart=dict(
                speaker_uuid='fixture', continuity='matched', display_label='Fixture')),
        cases=[dict(case=name, finals=[{}], observations=[{}],
            terminal=dict(status='completed'), interruption=receipt('stopped'),
            recovery=receipt('drained')) for name in CASES],
        score=dict(passed=True, cases={name:dict(passed=True) for name in CASES}))


def test_complete_recorded_result_preserves_scope():
    validate_result(sample())


@pytest.mark.parametrize('path,value', [
    (('passed',), False), (('error',), 'failed'), (('cleanup_error',), 'alive'),
    (('broker_errors',), ['fault']), (('registry_broker_error',), 'fault'),
    (('process_retired',), False), (('exit_code',), True), (('exit_code',), 1),
    (('registry_broker_retired',), False), (('elapsed_seconds',), float('nan')),
    (('installed',), True), (('persistent_uid_qualified',), True),
    (('registry','post_session_naming'), False),
    (('registry','process_restart_qualified'), False),
    (('registry','confirmed_forget_after_close'), False),
    (('registry','acoustic_match_after_restart','continuity'), 'provisional'),
    (('cases',0,'observations'), []), (('cases',0,'terminal','status'), 'aborted'),
    (('cases',0,'recovery','observation','outcome'), 'stopped'),
    (('cases',0,'interruption','result','accepted'), False),
    (('cases',0,'recovery','observation','delivered_samples'), 0),
    (('score','cases','solo_a','passed'), False),
])
def test_incomplete_result_is_refused(path, value):
    proof = sample()
    target = proof
    for key in path[:-1]: target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError): validate_result(proof)


def test_duplicate_case_is_not_a_complete_panel():
    proof = sample()
    proof['cases'][-1] = copy.deepcopy(proof['cases'][0])
    with pytest.raises(ValueError): validate_result(proof)


def test_byte_binding_required_not_only_green_summary(tmp_path):
    proof = sample()
    scripts = Path(__file__).resolve().parents[1]/'scripts'
    frozen = tmp_path/'freeze.json'
    frozen.write_text('{}')
    proof.update(checkpoint=str(tmp_path), runtime_manifest_sha256='runtime',
                 sdk_revision='sdk', bindings={str(frozen):sha(frozen)})
    for name in ('prove_sdk_separated_hearing.py', 'speaker_registry_test_host.py'):
        proof['bindings'][str(scripts/name)] = sha(scripts/name)
    evidence = tmp_path/'evidence.json'
    def save():
        evidence.write_text(json.dumps(proof))
        return sha(evidence)
    required = {str(frozen):sha(frozen)}
    with patch('scripts.verify_separated_signing_input.verify_checkpoint',
               return_value=({'runtime_manifest_sha256':'runtime'}, {}, {'revision':'sdk'}, required)):
        assert verify(tmp_path, evidence, save())['installed'] is False
        proof['bindings'].pop(str(scripts/'speaker_registry_test_host.py'))
        with pytest.raises(ValueError, match='required'): verify(tmp_path, evidence, save())
