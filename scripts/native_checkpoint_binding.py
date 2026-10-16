"""Validate a zero-argument native checkpoint against the current sealed SDK.

An integrity inventory is not a publisher signature or installed-host proof.
Returned paths are evidence inputs, never enrollment or live identity storage.
"""
import hashlib
import json
from pathlib import Path

from scripts.build_plugin_carrier import inputs, verify_sdk
from scripts.package_native_runtime import safe_relative, verify


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_checkpoint(root):
    root = Path(root).resolve()
    frozen = json.loads((root / 'freeze.json').read_text())
    record = json.loads((root / 'carrier-build.json').read_text())
    pin, _ = verify_sdk()
    if not frozen['passed'] or frozen['signed'] or frozen['installed']:
        raise ValueError('an unsigned uninstalled checkpoint is required')
    if record['sdk_revision'] != pin['revision'] or record['inputs'] != inputs():
        raise ValueError('checkpoint carrier does not use the current sealed source')
    binding = frozen['runtime_manifest_sha256']
    if record['runtime_manifest_sha256'] != binding:
        raise ValueError('carrier/runtime binding differs')
    runtime = root / 'runtime'
    profile = verify(runtime, binding)
    carrier = runtime / ('aii-voice-t3.exe' if profile['platform'] == 'windows' else 'aii-voice-t3')
    if carrier.is_symlink() or sha(carrier) != frozen['carrier_sha256'] or sha(carrier) != record['carrier_sha256']:
        raise ValueError('checkpoint carrier bytes differ')
    bound = {str(root / n): sha(root / n) for n in ('freeze.json', 'carrier-build.json')}
    bound[str(runtime / 'voice-runtime.json')] = binding
    bound[str(carrier)] = sha(carrier)
    for name in profile['files']:
        bound[str(runtime / name)] = sha(runtime / name)
    data = Path(frozen['models_root'])
    if data.is_symlink():
        raise ValueError('model root cannot be a symlink')
    for name, row in frozen['models'].items():
        safe_relative(name)
        path = data / name
        if path.is_symlink() or path.stat().st_size != row['bytes'] or sha(path) != row['sha256']:
            raise ValueError('checkpoint model bytes differ: ' + name)
        bound[str(path)] = row['sha256']
    return frozen, record, pin, bound
