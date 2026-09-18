"""AI-visible speaker arguments must explain nested parameters, not just names."""
import json
from pathlib import Path

import pytest

SCHEMAS = Path(__file__).resolve().parents[1] / 'plugin/native/schemas'


def described_properties(schema, path):
    for name, prop in schema.get('properties', {}).items():
        key = path + '.' + name
        assert prop.get('type') and prop.get('description', '').strip(), key
        described_properties(prop, key)
    items = schema.get('items')
    if isinstance(items, dict):
        described_properties(items, path + '[]')


@pytest.mark.parametrize('path', sorted(SCHEMAS.glob('speaker-*.input.json')), ids=lambda p: p.name)
def test_every_speaker_argument_explains_its_input(path):
    described_properties(json.loads(path.read_text()), path.stem)


def test_recovery_describes_two_exact_observed_hashes_and_no_open_session():
    root = json.loads((SCHEMAS / 'speaker-reset.input.json').read_text())
    recovery = root['properties']['recovery']
    assert set(recovery['required']) == {'enrollment_sha256', 'captures_sha256'}
    assert recovery['additionalProperties'] is False
    assert 'speaker.list.recovery' in recovery['description']
    assert 'closed' in recovery['description'] and 'operator' in recovery['description']
    for key in recovery['required']:
        prop = recovery['properties'][key]
        assert prop['pattern'] == '^([0-9a-f]{64}|absent)$'
        assert key in prop['description'] and 'absent' in prop['description']
