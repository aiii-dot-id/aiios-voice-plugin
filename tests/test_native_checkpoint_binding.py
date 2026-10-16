"""Small real inventories, with only the external SDK source pin substituted."""
import json

import pytest

from scripts import native_checkpoint_binding as binding
from scripts.package_native_runtime import runtime_inventory


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    root = tmp_path / 'checkpoint'; root.mkdir()
    runtime = root / 'runtime'; runtime.mkdir()
    (runtime / 'worker').write_bytes(b'worker')
    (runtime / 'aii-voice-t3').write_bytes(b'carrier')
    profile = {'schema': 'aiii.voice.native-runtime', 'qualified': False,
               'platform': 'macos', 'files': runtime_inventory(runtime)}
    (runtime / 'voice-runtime.json').write_text(json.dumps(profile))
    data = root / 'data'; data.mkdir(); model = data / 'model.bin'; model.write_bytes(b'weights')
    pin = {'revision': 'a' * 40}
    source = {'source.go': 'b' * 64}
    monkeypatch.setattr(binding, 'verify_sdk', lambda: (pin, {}))
    monkeypatch.setattr(binding, 'inputs', lambda: source)
    frozen = {'passed': True, 'signed': False, 'installed': False,
              'runtime_manifest_sha256': binding.sha(runtime / 'voice-runtime.json'),
              'carrier_sha256': binding.sha(runtime / 'aii-voice-t3'),
              'models_root': str(data), 'models': {'model.bin': {'bytes': 7, 'sha256': binding.sha(model)}}}
    record = {'sdk_revision': pin['revision'], 'inputs': source,
              'runtime_manifest_sha256': frozen['runtime_manifest_sha256'],
              'carrier_sha256': frozen['carrier_sha256']}
    (root / 'freeze.json').write_text(json.dumps(frozen))
    (root / 'carrier-build.json').write_text(json.dumps(record))
    return root


def test_bound_checkpoint_includes_model_carrier_and_runtime(checkpoint):
    frozen, record, pin, bound = binding.verify_checkpoint(checkpoint)
    assert record['sdk_revision'] == pin['revision']
    assert str(checkpoint / 'data/model.bin') in bound
    assert str(checkpoint / 'runtime/worker') in bound
    assert bound[str(checkpoint / 'runtime/aii-voice-t3')] == frozen['carrier_sha256']


@pytest.mark.parametrize('damage', ['sdk', 'source', 'binding', 'carrier', 'model', 'extra-runtime', 'model-link'])
def test_checkpoint_mutations_are_refused(checkpoint, damage):
    record_path = checkpoint / 'carrier-build.json'
    record = json.loads(record_path.read_text())
    if damage == 'sdk': record['sdk_revision'] = 'c' * 40
    elif damage == 'source': record['inputs'] = {}
    elif damage == 'binding': record['runtime_manifest_sha256'] = '0' * 64
    elif damage == 'carrier': (checkpoint / 'runtime/aii-voice-t3').write_bytes(b'changed')
    elif damage == 'model': (checkpoint / 'data/model.bin').write_bytes(b'changed')
    elif damage == 'extra-runtime': (checkpoint / 'runtime/extra').write_bytes(b'extra')
    elif damage == 'model-link':
        model = checkpoint / 'data/model.bin'; target = model.with_name('other.bin')
        model.rename(target); model.symlink_to(target)
    record_path.write_text(json.dumps(record))
    with pytest.raises(ValueError): binding.verify_checkpoint(checkpoint)
