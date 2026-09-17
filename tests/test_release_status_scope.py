import pytest
from scripts.stage_beta1_signed_publication import handoff_status


def test_staging_does_not_invent_a_beta_or_installation_verdict():
    status = handoff_status({'distribution_review_complete': False, 'open_items': ['UID disposition']})
    assert status['artifact_integrity'] == 'verified'
    assert status['distribution_review'] == 'open'
    assert status['technical_acceptance'] == 'not_assessed_by_staging'
    assert status['publication'] == 'not_published_by_staging'
    assert status['installation'] == 'not_performed_by_staging'
    assert 'beta_release_ready' not in status
    assert 'remaining_gates' not in status


def test_distribution_completion_does_not_claim_technical_qualification():
    status = handoff_status({'distribution_review_complete': True, 'open_items': []})
    assert status['distribution_review'] == 'complete'
    assert status['technical_acceptance'] == 'not_assessed_by_staging'


@pytest.mark.parametrize('index', [
    {'distribution_review_complete': True, 'open_items': ['still unresolved']},
    {'distribution_review_complete': 'true', 'open_items': []},
    {'distribution_review_complete': False, 'open_items': 'unstructured'},
    {'distribution_review_complete': False, 'open_items': [False]},
])
def test_inconsistent_distribution_labels_are_refused(index):
    with pytest.raises(ValueError):
        handoff_status(index)
