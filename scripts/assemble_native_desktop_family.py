"""One private version, three native variants, no signing or publication.

Builds from hash-bound single-platform checkpoint packages, not development
binaries. All shared settings and schemas must agree. Model paths may be
deduplicated only when the complete declaration agrees. Platform companions
are retained byte-for-byte and referenced by their existing declarations.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess

from scripts.audit_native_desktop_packages import tar_files, assert_binary_target
from scripts.build_plugin_carrier import ROOT, SDK_SOURCE, verify_sdk


def digest(raw): return hashlib.sha256(raw).hexdigest()
def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def parent(root):
    root = Path(root).resolve()
    handoff = json.loads((root / 'handoff.json').read_text())
    assert handoff['passed'] and not handoff['signed']
    path = root / handoff['bundle']['bundle']
    assert sha(path) == handoff['bundle']['sha256'] and path.stat().st_size == handoff['bundle']['bytes']
    members, modes = tar_files(path)
    prefix = path.name.removesuffix('.aiiospkg') + '/'
    manifest = json.loads(members[prefix + 'manifest.json'])
    files = {n[len(prefix + 'install-root/'):]: raw for n, raw in members.items() if n.startswith(prefix + 'install-root/')}
    assert set(members) == {prefix + 'manifest.json'} | {prefix + 'install-root/' + n for n in files}
    aggregate = hashlib.sha256()
    for name, raw in sorted(files.items()): aggregate.update((name + '\0' + digest(raw) + '\n').encode())
    assert 'sha256:' + aggregate.hexdigest() == manifest['package_hash'] == handoff['bundle']['package_hash']
    assert set(modes.values()) == {0o644}
    assert len(manifest['variants']) == 1
    variant = manifest['variants'][0]
    assert variant['platform'] == handoff['platform']
    carrier = files[variant['entrypoint']]
    assert 'sha256:' + digest(carrier) == variant['artifact_hash']
    if variant['platform'] == 'macos':
        assert carrier[:4] == bytes.fromhex('cffaedfe') and carrier[4:8] == bytes.fromhex('0c000001')
    else: assert_binary_target(carrier, variant['platform'])
    declaration = json.loads(files['runtime.json'])['runtimes']
    assert len(declaration) == 1 and declaration[0]['variant_id'] == variant['variant_id']
    runtime = root / (variant['variant_id'] + '-runtime.tar.gz')
    for name in ('sha256', 'size', 'files', 'installed_bytes', 'inventory_sha256'):
        assert declaration[0][name] == handoff['runtime_archive'][name]
    assert sha(runtime) == declaration[0]['sha256'] and runtime.stat().st_size == declaration[0]['size']
    # A validated digest is not itself an inference qualification claim.
    return {'root': root, 'handoff': handoff, 'manifest': manifest, 'files': files,
            'variant': variant, 'runtime': runtime, 'declaration': declaration[0], 'carrier': carrier}


def compose(template, parents, version):
    assert {p['variant']['platform'] for p in parents} == {'macos', 'linux', 'windows'} and len(parents) == 3
    cfg = copy.deepcopy(template)
    cfg.update(version=version, title='AII Voice CP3 — native desktop test family',
               description='Private English-language native desktop checkpoint: ten stable voices, active adjustable VAD, speaker enrollment and barge-in/recovery. Speaker identity grants no authority. Platform execution proofs are recorded separately; public distribution, physical installed qualification and mobile qualification remain incomplete.')
    cfg['variants'], cfg['runtimes'], cfg['models'] = [], [], []
    shared = None; models = {}; model_names = {}; payloads = {}
    for p in parents:
        manifest, files, variant = p['manifest'], p['files'], p['variant']
        assert manifest['id'] == cfg['id'] and manifest['plugin_family'] == cfg['plugin_family']
        assert manifest['capability_envelope'] == cfg['capability_envelope']
        observed = {name: json.loads(raw) for name, raw in files.items()
                    if name == 'settings.json' or name.startswith(('interfaces/','schemas/'))}
        if shared is None: shared = observed
        assert observed == shared, 'settings or method schemas differ across platforms'
        assert observed['settings.json'] == cfg['settings']
        own = json.loads(files['models.json'])
        for model in own:
            key = model['path'].casefold()
            assert key not in models or model == models[key], 'model destination collision'
            assert model['name'] not in model_names or model_names[model['name']] == key, 'model id collision'
            models[key] = model; model_names[model['name']] = key
        v = {key: variant[key] for key in ('variant_id', 'platform', 'arch', 'topology',
              'execution_runtime', 'admission_profile', 'variant_capabilities')}
        v['accelerator'] = json.loads(files['accelerator.json'])[variant['variant_id']]
        assert set(v['accelerator']['models']) == {m['name'] for m in own}
        v['artifact'] = 'payloads/' + variant['variant_id']
        payloads[v['artifact']] = p['carrier']
        cfg['variants'].append(v); cfg['runtimes'].append(copy.deepcopy(p['declaration']))
    cfg['models'] = sorted(models.values(), key=lambda m: m['path'])
    return cfg, payloads, shared


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('macos', 'linux', 'windows', 'out', 'windows-audit'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--version', required=True)
    a = p.parse_args()
    for name in ('macos', 'linux', 'windows', 'out', 'windows_audit'):
        setattr(a, name, getattr(a, name).resolve())
    pin, _ = verify_sdk()
    approved = json.loads(a.windows_audit.read_text())
    assert approved['passed'] and approved['family_namespace_qualified']
    parents = [parent(getattr(a, name)) for name in ('macos', 'linux', 'windows')]
    assert parents[2]['handoff']['bundle']['sha256'] == approved['package']['bundle_sha256']
    assert all(x['handoff']['sdk_revision'] == pin['revision'] for x in parents)
    template = json.loads((a.macos / 'plugin.json').read_text())
    cfg, payloads, shared = compose(template, parents, a.version)
    a.out.mkdir(parents=True, exist_ok=False)
    for name, raw in payloads.items():
        path = a.out / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    (a.out / 'plugin.json').write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + '\n')
    descriptors = json.loads((a.macos / 'descriptors.json').read_text())
    assert {d['id'] for d in descriptors} == {d['id'] for n, rows in shared.items() if n.startswith('interfaces/') for d in rows}
    (a.out / 'descriptors.json').write_text(json.dumps(descriptors, indent=2) + '\n')
    for name in shared:
        if name.startswith('schemas/'):
            path=a.out/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(parents[0]['files'][name])
    assembler = a.out / 'assemble'
    env = {**os.environ, 'GOTOOLCHAIN': 'local', 'GOWORK': 'off', 'GOPROXY': 'off'}
    subprocess.run(['/usr/local/go1.27/bin/go', 'build', '-trimpath', '-buildvcs=false', '-o', str(assembler),
                    str(ROOT / 'scripts/private_cp1_package.go')], cwd=SDK_SOURCE, env=env, check=True, timeout=120)
    run = subprocess.run([str(assembler), str(a.out)], capture_output=True, timeout=30)
    (a.out / 'assemble.stdout').write_bytes(run.stdout); (a.out / 'assemble.stderr').write_bytes(run.stderr)
    assert run.returncode == 0, 'retained assembler refusal'
    assembly = json.loads(run.stdout)
    bundle = a.out / assembly['bundle']; assert sha(bundle) == assembly['sha256']
    files, _ = tar_files(bundle); prefix = bundle.name.removesuffix('.aiiospkg') + '/install-root/'
    for q in parents:
        assert files[prefix + q['variant']['entrypoint']] == q['carrier']
    assert json.loads(files[prefix + 'models.json']) == cfg['models']
    result = {'passed': True, 'scope': __doc__, 'bundle': assembly, 'version': a.version,
              'sdk_revision': pin['revision'], 'variants': [v['variant_id'] for v in cfg['variants']],
              'builder_sha256': sha(Path(__file__)),
              'assembler_sha256': sha(ROOT / 'scripts/private_cp1_package.go'),
              'parent_handoff_sha256': {q['variant']['platform']: sha(q['root'] / 'handoff.json') for q in parents},
              'models': len(cfg['models']), 'unique_model_bytes': sum(m['size'] for m in cfg['models']),
              'per_variant_models': {v['variant_id']: v['accelerator']['models'] for v in cfg['variants']},
              'companions': {q['variant']['variant_id']: {'path': str(q['runtime']), **q['declaration']} for q in parents},
              'parent_bundles': {q['variant']['platform']: q['handoff']['bundle']['sha256'] for q in parents},
              'windows_audit_sha256': sha(a.windows_audit), 'signed': False, 'installed': False, 'published': False,
              'human_level_qualified': False, 'public_distribution_ready': False,
              'public_blockers': ['placeholder model/runtime URLs', 'full redistribution notices',
                                  'new family signing and installed-host gates', 'mobile qualification']}
    (a.out / 'handoff.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('passed', 'bundle', 'variants', 'models', 'unique_model_bytes', 'signed')}))


if __name__ == '__main__': main()
