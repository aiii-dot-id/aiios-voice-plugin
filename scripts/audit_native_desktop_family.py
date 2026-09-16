"""Read back the actual unified package and every referenced runtime archive.

Integrity and preserved checkpoint composition only: no signature, installed
physical audio, multilingual, mobile or human-level qualification is implied.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from scripts.audit_native_desktop_packages import tar_files, assert_binary_target


def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()
def digest(raw): return hashlib.sha256(raw).hexdigest()


def executable_binding(raw, expected, go='/usr/local/go1.27/bin/go'):
    """Read the initialized string from the actual binary's symbol/sections."""
    assert len(expected) == 64
    with tempfile.TemporaryDirectory(prefix='voice-binding-') as directory:
        path = Path(directory) / 'carrier'; path.write_bytes(raw)
        result = subprocess.run([go, 'run', str(Path(__file__).with_name('inspect_carrier_binding.go')), str(path), expected],
                                env={**os.environ, 'GOTOOLCHAIN': 'local', 'GOWORK': 'off', 'GOPROXY': 'off'},
                                text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, 'missing/wrong compiled runtime binding: ' + result.stderr[:400]
    assert json.loads(result.stdout) == {'passed': True, 'runtime_manifest_sha256': expected}
    return expected


def audit(family, *, macos_backend='cpu'):
    # The expected backend is supplied by the independent caller, never inferred
    # from a possibly false package declaration. Historical CPU audits retain
    # their exact expectation; the Metal candidate is admitted explicitly.
    assert macos_backend in ('cpu', 'metal'), 'unsupported expected Mac backend'
    family = Path(family)
    handoff = json.loads((family / 'handoff.json').read_text())
    bundle = family / handoff['bundle']['bundle']
    assert sha(bundle) == handoff['bundle']['sha256'] and bundle.stat().st_size == handoff['bundle']['bytes']
    members, modes = tar_files(bundle)
    root = bundle.name.removesuffix('.aiiospkg') + '/'
    manifest = json.loads(members[root + 'manifest.json'])
    files = {n[len(root + 'install-root/'):]: raw for n, raw in members.items() if n.startswith(root + 'install-root/')}
    assert set(members) == {root + 'manifest.json'} | {root + 'install-root/' + n for n in files}
    assert set(modes.values()) == {0o644}
    aggregate = hashlib.sha256()
    for n, raw in sorted(files.items()): aggregate.update((n + '\0' + digest(raw) + '\n').encode())
    assert 'sha256:' + aggregate.hexdigest() == manifest['package_hash'] == handoff['bundle']['package_hash']
    manifest_view = {k: v for k, v in manifest.items() if k != 'package_hash'}
    assert 'sha256:' + digest(json.dumps(manifest_view, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()) == handoff['bundle']['manifest_hash']
    assert manifest['id'] == 'id.aiii.voice' and manifest['version'] == handoff['version']
    assert manifest['capability_envelope'] == ['fs.private']
    want = {'macos-arm64-native': ('macos', 'arm64', macos_backend),
            'linux-x86_64-native': ('linux', 'x86_64', 'vulkan'),
            'windows-x86_64-native': ('windows', 'x86_64', 'vulkan')}
    assert len(manifest['variants']) == 3 and {v['variant_id'] for v in manifest['variants']} == set(want)
    models = json.loads(files['models.json']); assert len(models) == 25
    assert len({m['path'].casefold() for m in models}) == len({m['name'] for m in models}) == 25
    by_name = {m['name']: m for m in models}
    runtimes = json.loads(files['runtime.json'])['runtimes']
    assert len(runtimes) == 3 and {r['variant_id'] for r in runtimes} == set(want)
    accelerators = json.loads(files['accelerator.json']); assert set(accelerators) == set(want)
    methods = set(); expected_files = {'accelerator.json', 'models.json', 'runtime.json', 'settings.json'}
    assert len(manifest['interfaces']['core']) == 2
    for interface in manifest['interfaces']['core']:
        name = 'interfaces/' + interface['id'] + '.v1.schema.json'; expected_files.add(name)
        assert 'sha256:' + digest(files[name]) == interface['schema_hash']
        schema = json.loads(files[name]); assert [d['id'] for d in schema] == interface['methods']
        for d in schema:
            assert d['id'] not in methods; methods.add(d['id'])
            for field in ('input', 'output'):
                ref = d.get(field)
                if ref:
                    assert ref.startswith('schemas/') and not Path(ref).is_absolute()
                    assert '..' not in Path(ref).parts and '\\' not in ref
                    expected_files.add(ref)
                    declared = json.loads(files[ref])
                    assert isinstance(declared, dict) and declared.get('type') == 'object'
            if d['id'].startswith('speaker.'):
                assert interface['id'] == 'speaker.uid'
                assert d.get('operator_confirms', False) == (d['id'] != 'speaker.list')
            else: assert interface['id'] == 'speech.session' and d['id'].startswith('speech.session.')
    assert methods == set(handoff['bundle']['methods']) and len(methods) == 12
    settings = json.loads(files['settings.json']); assert len(settings) == 7
    assert next(s for s in settings if s['key'] == 'tts_voice')['labels']['eponine'] == 'Éponine'
    variants = {}
    for v in manifest['variants']:
        key = v['variant_id']; platform, arch, backend = want[key]; expected_files.add(v['entrypoint'])
        assert (v['platform'], v['arch']) == (platform, arch) and v['execution_runtime'] == 'native_t3_component'
        raw = files[v['entrypoint']]; assert 'sha256:' + digest(raw) == v['artifact_hash']
        if platform == 'macos': assert raw[:8] == bytes.fromhex('cffaedfe0c000001')
        else: assert_binary_target(raw, platform)
        accelerator = accelerators[key]
        assert (accelerator['os'], accelerator['arch'], accelerator['backend']) == (platform, arch, backend)
        assert accelerator['fallback'] == 'none' and accelerator['session_limit'] == 1
        assert len(accelerator['models']) == len(set(accelerator['models'])) == 24
        own = {by_name[n]['path']: by_name[n] for n in accelerator['models']}
        coeff = 'endpoint/windows/coefficients.f32' if platform == 'windows' else 'endpoint/coefficients.f32'
        expected_sha = '7f4b0550b20f784d3de0d06ac321ba11509748b5c7f9360afd6e9d448636d641' if platform == 'windows' else 'bca0b4a40ed989154538454336c3d1ebd3fd120bbc29272a8e54f676a467bafc'
        assert own[coeff]['sha256'] == expected_sha
        declaration = next(r for r in runtimes if r['variant_id'] == key)
        companion = Path(handoff['companions'][key]['path'])
        assert sha(companion) == declaration['sha256'] and companion.stat().st_size == declaration['size']
        packed, packed_modes = tar_files(companion)
        inv_raw = packed['runtime/inventory.json']; assert digest(inv_raw) == declaration['inventory_sha256']
        inv = json.loads(inv_raw)['files']; assert len(inv) == declaration['files']
        assert set(packed) == {'runtime/inventory.json'} | {'runtime/' + r['path'] for r in inv}
        assert sum(r['size'] for r in inv) == declaration['installed_bytes']
        for row in inv:
            contents = packed['runtime/' + row['path']]
            assert len(contents) == row['size'] and 'sha256:' + digest(contents) == row['sha256']
            assert packed_modes['runtime/' + row['path']] == (0o755 if row['mode'] == 'exec' else 0o644)
        native = json.loads(packed['runtime/native-profile.json'])
        assert native['backend'] == backend, 'runtime backend differs from declared accelerator'
        binding = executable_binding(raw, digest(packed['runtime/voice-runtime.json']))
        assert native['models']['endpoint_coefficients'] == coeff
        assert json.loads(packed['runtime/resources/settings.json']) == settings
        for role, path in native['models'].items():
            assert path in own or (role in ('asr', 'tts') and any(n.startswith(path + '/') for n in own)), ('undeclared runtime model', key, path)
        variants[key] = {'carrier_sha256': digest(raw), 'runtime_sha256': declaration['sha256'],
                         'runtime_archive_bytes': declaration['size'], 'runtime_installed_bytes': declaration['installed_bytes'],
                         'models': len(own), 'model_bytes': sum(m['size'] for m in own.values()), 'backend': backend,
                         'compiled_runtime_manifest_sha256': binding}
    assert set(files) == expected_files
    result = {'passed': True, 'scope': __doc__, 'bundle_sha256': sha(bundle), 'variants': variants,
              'unique_models': 25, 'unique_model_bytes': sum(m['size'] for m in models),
              'windows_separate_model_overhead_bytes': 65920, 'methods': sorted(methods),
              'settings': len(settings), 'voices': len(next(s for s in settings if s['key'] == 'tts_voice')['values']),
              'signed': False, 'installed': False, 'published': False, 'human_level_qualified': False,
              'public_distribution_ready': False, 'auditor_sha256': sha(Path(__file__))}
    result['binding_inspector_sha256'] = sha(Path(__file__).with_name('inspect_carrier_binding.go'))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--family', type=Path, required=True); p.add_argument('--out', type=Path, required=True)
    a = p.parse_args(); assert not a.out.exists()
    result = audit(a.family)
    with a.out.open('x') as f: json.dump(result, f, indent=2); f.write('\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
