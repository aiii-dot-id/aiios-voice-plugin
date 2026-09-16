import copy
import json
from pathlib import Path

import pytest

from scripts.audit_native_enrollment_desktops import sdk_identity

ROOT=Path(__file__).resolve().parents[1]


def actual():
    proof=ROOT/'deliverables/cp3-enrollment-asr-refresh-packaged-20260913-r2/enrollment/result.json'
    return json.loads(proof.read_text()),json.loads((ROOT/'plugin/sdk-source.json').read_text())


def test_completed_landed_sdk_is_bound_to_its_archive():
    assert sdk_identity(*actual()) is True


@pytest.mark.parametrize('field,value',[
    ('passed',False),('exit_code',1),('sdk_source_landed',False),
    ('release_dependency_qualified',False),('sdk_revision','0'*40),
    ('sdk_source_archive_sha256','0'*64),('sdk_candidate_only',True),
])
def test_label_or_wrong_revision_cannot_close_the_sdk_gate(field,value):
    record,pin=actual();record=copy.deepcopy(record);record[field]=value
    with pytest.raises(AssertionError):sdk_identity(record,pin)


def test_candidate_remains_candidate_not_a_release_dependency():
    record,pin=actual();record.update(sdk_candidate_only=True,release_dependency_qualified=False,
                                    sdk_revision=pin['revision']+'+isolated-confirmation-serialization-candidate')
    assert sdk_identity(record,pin) is False
