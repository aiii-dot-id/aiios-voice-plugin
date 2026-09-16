import copy
import hashlib
import json
from pathlib import Path
import pytest

from scripts.assemble_native_desktop_family import compose, parent
from scripts.audit_native_family_windows import derivation

ROOT = Path(__file__).resolve().parents[1]
MAC = ROOT / 'deliverables/checkpoints/cp3-host-metadata-macos-20260913-r1'


def declarations():
    # Composition fixtures only; actual platform headers/hashes are verified
    # separately by parent() before any real artifact reaches compose().
    mac = parent(MAC)
    cfg = json.loads((MAC / 'plugin.json').read_text())
    rows = []
    for platform, arch in [('macos', 'arm64'), ('linux', 'x86_64'), ('windows', 'x86_64')]:
        p = copy.deepcopy(mac)
        old = p['variant']['variant_id']; new = platform + '-' + arch + '-native'
        p['variant'].update(platform=platform, arch=arch, variant_id=new)
        p['carrier'] = platform.encode()
        accel = json.loads(p['files']['accelerator.json'])[old]
        accel.update(os=platform, arch=arch)
        models = json.loads(p['files']['models.json'])
        if platform == 'windows':
            m = next(m for m in models if m['path'] == 'endpoint/coefficients.f32')
            previous = m['name']; m['path'] = 'endpoint/windows/coefficients.f32'; m['name'] += '-win'
            m['sha256'] = '7' * 64
            accel['models'] = [m['name'] if n == previous else n for n in accel['models']]
        p['files']['models.json'] = json.dumps(models).encode()
        p['files']['accelerator.json'] = json.dumps({new: accel}).encode()
        p['declaration']['variant_id'] = new
        rows.append(p)
    return cfg, rows


def test_three_platform_artifacts_share_only_identical_model_declarations():
    cfg, rows = declarations()
    result, payloads, _ = compose(cfg, rows, '0.1.0-test')
    assert len(result['variants']) == 3 and len(result['models']) == 25
    assert len({m['path'].lower() for m in result['models']}) == 25
    assert all(len(v['accelerator']['models']) == 24 for v in result['variants'])
    assert set(payloads.values()) == {b'macos', b'linux', b'windows'}


def test_family_holds_referenced_operation_schema_bytes_together():
    cfg,rows=declarations()
    for row in rows:row['files']['schemas/example.input.json']=b'{"type":"object"}'
    _,_,shared=compose(cfg,rows,'0.1.0-test')
    assert shared['schemas/example.input.json']=={'type':'object'}
    rows[-1]['files']['schemas/example.input.json']=b'{"type":"string"}'
    with pytest.raises(AssertionError):compose(cfg,rows,'0.1.0-test')


@pytest.mark.parametrize('damage', ['path-collision', 'settings', 'method-schema', 'model-scope', 'missing-platform'])
def test_family_rejects_silent_substitution_or_contract_drift(damage):
    cfg, rows = declarations(); p = rows[-1]
    if damage == 'path-collision':
        models = json.loads(p['files']['models.json'])
        next(m for m in models if 'windows' in m['path'])['path'] = 'endpoint/coefficients.f32'
        p['files']['models.json'] = json.dumps(models).encode()
    elif damage == 'settings':
        data = json.loads(p['files']['settings.json']); data[0]['default'] = 'marius'
        p['files']['settings.json'] = json.dumps(data).encode()
    elif damage == 'method-schema':
        name = next(n for n in p['files'] if n.startswith('interfaces/'))
        p['files'][name] = b'[]'
    elif damage == 'model-scope':
        data = json.loads(p['files']['accelerator.json']); data[p['variant']['variant_id']]['models'].pop()
        p['files']['accelerator.json'] = json.dumps(data).encode()
    else: rows.pop()
    with pytest.raises(AssertionError): compose(cfg, rows, '0.1.0-test')


def derivation_fixture():
    h = lambda raw: hashlib.sha256(raw).hexdigest()
    data = {'sha256': '7f4b0550b20f784d3de0d06ac321ba11509748b5c7f9360afd6e9d448636d641', 'bytes': 65920}
    old = {'models': {'endpoint/coefficients.f32': data}, 'library_hashes': {'native.dll': 'a' * 64}, 'carrier_sha256': 'b' * 64}
    old_raw = json.dumps(old).encode()
    old_native = b'{"models":{"endpoint_coefficients":"endpoint/coefficients.f32"}}'
    native = old_native.replace(b'endpoint/coefficients', b'endpoint/windows/coefficients')
    settings = json.dumps([{'labels': {'eponine': 'Éponine'}, 'default': 'alba'}]).encode()
    old_settings = json.dumps([{'labels': {'eponine': 'Ã‰ponine'}, 'default': 'alba'}]).encode()
    old_profile = {'files': {n: {'sha256': h(raw), 'bytes': len(raw), 'executable': False}
                            for n, raw in [('native-profile.json', old_native), ('resources/settings.json', old_settings), ('bin/native.dll', b'unchanged')]}}
    profile = copy.deepcopy(old_profile)
    for n, raw in [('native-profile.json', native), ('resources/settings.json', settings)]:
        profile['files'][n].update(sha256=h(raw), bytes=len(raw))
    frozen = {**old, 'parent_freeze_sha256': h(old_raw), 'models': {'endpoint/windows/coefficients.f32': data},
              'carrier_sha256': 'c' * 64, 'settings_sha256': h(settings)}
    return [frozen, old, old_raw, profile, old_profile, native, old_native, settings, old_settings]


@pytest.mark.parametrize('damage', [None, 'model-bytes', 'native-binary', 'setting-default', 'wrong-parent'])
def test_independent_derivation_audit_rejects_unrelated_changes(damage):
    args = derivation_fixture()
    if damage == 'model-bytes':
        args[0]['models'] = copy.deepcopy(args[0]['models'])
        next(iter(args[0]['models'].values()))['sha256'] = 'd' * 64
    elif damage == 'native-binary': args[3]['files']['bin/native.dll']['sha256'] = 'd' * 64
    elif damage == 'setting-default': args[7] = args[7].replace(b'alba', b'other')
    elif damage == 'wrong-parent': args[0]['parent_freeze_sha256'] = 'd' * 64
    if damage:
        with pytest.raises(AssertionError): derivation(*args)
    else: derivation(*args)
