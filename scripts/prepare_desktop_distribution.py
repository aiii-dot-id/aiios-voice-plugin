"""Stage the exact desktop release assets and assemble an unsigned URL-bound package.

No publishing, signing, installed state changes, network requests or inference.
An explicit asset base URL is required at assembly; none is selected implicitly.
"""
import argparse
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import quote, urlsplit

from scripts.build_plugin_carrier import ROOT, SDK_SOURCE, verify_sdk
from scripts.repackage_native_schemas import read_package
from scripts.prepare_desktop_release_notices import validate_bundle, validate_runtime_archive

PARENT = ROOT / 'deliverables/checkpoints/desktop-beta-unified-20260914-r5'
PACKAGE_SHA = 'd331620b03ed586a5c2764d236af90a48a60caf8365d827296e0fe004f71cabd'
INVENTORY = ROOT / 'deliverables/desktop-release-dependencies-20260914-r1/inventory.json'
INVENTORY_SHA = 'ccd12c31e9b0005723b4053b1003f1bc82fcdb9432e5bc6cdaa2926f88f503de'
NOTICES = ROOT / 'deliverables/desktop-release-notices-20260914-r4/bundle'
NOTICES_SHA = '83caa6381a6ca0d8c139cad894f3db552fc38ab2cf5ca485a344dba385e758d4'
METAL_PACKAGE_SHA = '1196aa5de3f1a30ee4443f5bc25e887e805b4e61b58acca4d1961f4a45cab005'
IDLE_VAD_PACKAGE_SHA = 'eb68e947ebfe0b0a1514deba540ab31a0c19bbe3e676f67d4ddbcd7c445a7038'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def emit(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def copy_asset(source, target, size, expected):
    source = Path(source)
    if source.is_symlink() or not source.is_file() or source.stat().st_size != size:
        raise ValueError('asset source kind/size differs: ' + source.name)
    if sha(source) != expected:
        raise ValueError('asset source hash differs: ' + source.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Independent inodes: a future edit of release staging must not edit the
    # retained model/companion via a hard link. Only the 377 MB release set copies.
    with source.open('rb') as src, target.open('xb') as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    if target.stat().st_size != size or sha(target) != expected or sha(source) != expected:
        raise ValueError('asset changed during staging: ' + source.name)


def census(root):
    result = {}
    for p in root.rglob('*'):
        if p.is_symlink() or not (p.is_file() or p.is_dir()):
            raise ValueError('nonregular release input')
        if p.is_file():
            result[p.relative_to(root).as_posix()] = {'sha256': sha(p), 'size': p.stat().st_size}
    return result


def stage(out):
    if sha(INVENTORY) != INVENTORY_SHA or sha(NOTICES / 'manifest.json') != NOTICES_SHA:
        raise ValueError('release input inventory changed')
    inv = json.loads(INVENTORY.read_text())
    notices = validate_bundle(NOTICES)
    hand = json.loads((PARENT / 'handoff.json').read_text())
    package = PARENT / hand['bundle']['bundle']
    manifest, files = read_package(package, PACKAGE_SHA)
    cfg = json.loads((PARENT / 'plugin.json').read_text())
    if cfg['models'] != json.loads(files['models.json']) or cfg['runtimes'] != json.loads(files['runtime.json'])['runtimes']:
        raise ValueError('author config differs from tested declarations')
    if cfg['settings'] != json.loads(files['settings.json']):
        raise ValueError('author settings differ from tested package')
    assert inv['candidate_sha256'] == notices['candidate_sha256'] == PACKAGE_SHA
    out.mkdir(parents=True, exist_ok=False)
    author = out / 'author'
    put(out / 'parent.aiiospkg', package.read_bytes())
    # Parent retains its exact basename for read_package's canonical root check.
    (out / 'parent.aiiospkg').rename(out / package.name)
    variants = {v['variant_id']: v for v in manifest['variants']}
    for v in cfg['variants']:
        v['artifact'] = 'payloads/' + v['variant_id']
        put(author / v['artifact'], files[variants[v['variant_id']]['entrypoint']])
    descriptors = []
    for interface in manifest['interfaces']['core']:
        descriptors.extend(json.loads(files[f'interfaces/{interface["id"]}.v{interface["version"]}.schema.json']))
    emit(author / 'descriptors.json', sorted(descriptors, key=lambda d: d['id']))
    for name, raw in files.items():
        if name.startswith('schemas/'):
            put(author / name, raw)
    emit(author / 'plugin.json', cfg)
    # Copy original notices verbatim, including provenance-bearing model cards.
    # Collector code, local paths and private evidence records are not packaged.
    entries = []
    for name, row in sorted(notices['files'].items()):
        if not name.startswith('notices/'):
            continue
        raw = (NOTICES / name).read_bytes()
        put(author / name, raw)
        entries.append({'path': name, 'sha256': row['sha256'], 'size': row['bytes']})
    index = {
        'models': notices['models'],
        'libraries': [{k: v for k, v in row.items() if k in
                       ('component', 'distribution', 'platform', 'shipped_sha256', 'source_sha256', 'execution_claim')}
                      for row in notices['runtime_distribution_bindings']],
        'original_notice_files': entries,
        'scope': 'Original notices and attribution; collection does not establish redistribution clearance or relicense third-party models/libraries.',
        'distribution_review_complete': False,
        'open_items': notices['open_items'],
    }
    emit(author / 'notices/INDEX.json', index)
    p = author / 'notices/INDEX.json'
    entries.append({'path': 'notices/INDEX.json', 'sha256': sha(p), 'size': p.stat().st_size})
    emit(author / 'release-notices.json', entries)

    freeze = json.loads((PARENT / 'bound/macos-arm64-native/freeze.json').read_text())
    routes, assets = {}, {}
    for m in inv['models']:
        if m['source']:
            routes[m['name']] = {'url': m['source']['url']}
            continue
        name = m['release_asset_name']
        source = Path(freeze['models_root']) / m['path']
        if m['path'] == 'endpoint/windows/coefficients.f32':
            source = ROOT / 'deliverables/checkpoints/cp3-native-desktop-family-20260913-r1/model-overrides' / m['path']
        copy_asset(source, out / 'assets' / name, m['size'], m['sha256'])
        assets[name] = {'kind': 'model', 'model_path': m['path'], 'size': m['size'],
                        'sha256': m['sha256'], 'platforms': m['platforms']}
        routes[m['name']] = {'asset': name}
    runtime_routes = {}
    for key, r in inv['runtime'].items():
        validate_runtime_archive(r)
        a = r['archive']; name = a['sha256'] + '-' + key + '-runtime.tar.gz'
        copy_asset(a['path'], out / 'assets' / name, a['size'], a['sha256'])
        assets[name] = {'kind': 'runtime', 'size': a['size'], 'sha256': a['sha256'], 'platforms': [key]}
        runtime_routes[key] = {'asset': name}
    assert len(assets) == 12 and sum(r['size'] for r in assets.values()) == 377492271
    plan = {'parent_bundle': package.name, 'parent_sha256': PACKAGE_SHA,
            'inventory_sha256': INVENTORY_SHA, 'notice_manifest_sha256': NOTICES_SHA,
            'models': routes, 'runtimes': runtime_routes, 'assets': assets,
            'notice_files': len(entries), 'asset_base_url': None,
            'signed': False, 'installed': False, 'published': False, 'public_distribution_ready': False}
    emit(out / 'release-plan.json', plan)
    emit(out / 'staging-inventory.json', census(out))
    verify_staging(out)
    return plan


def verify_staging(root):
    expected = json.loads((root / 'staging-inventory.json').read_text())
    current = census(root)
    current.pop('staging-inventory.json')
    if current != expected:
        raise ValueError('release staging changed or contains extra files')
    plan = json.loads((root / 'release-plan.json').read_text())
    if plan['parent_sha256'] not in (PACKAGE_SHA, METAL_PACKAGE_SHA, IDLE_VAD_PACKAGE_SHA) or plan['inventory_sha256'] != INVENTORY_SHA or plan['notice_manifest_sha256'] != NOTICES_SHA:
        raise ValueError('release plan binds different inputs')
    if plan['parent_sha256'] == IDLE_VAD_PACKAGE_SHA and plan.get('profile') != 'idle-vad-checkpoint':
        raise ValueError('VAD checkpoint selection must be explicit')
    read_package(root / plan['parent_bundle'], plan['parent_sha256'])
    return plan


def base_url(value):
    p = urlsplit(value)
    host = (p.hostname or '').lower()
    if p.scheme != 'https' or not host or p.username or p.password or p.query or p.fragment or p.port not in (None, 443):
        raise ValueError('explicit public HTTPS asset directory required, without credentials/query/fragment')
    if any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value or '%' in p.path or '..' in p.path.split('/'):
        raise ValueError('ambiguous asset directory')
    if host in ('localhost', 'checkpoint.invalid') or host.endswith(('.invalid', '.local', '.localhost')):
        raise ValueError('placeholder/private asset directory refused')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError('private asset address refused')
    return value.rstrip('/') + '/'


def configure(staging, base, version):
    plan = verify_staging(staging)
    base = base_url(base)
    cfg = json.loads((staging / 'author/plugin.json').read_text())
    cfg['version'] = version
    def route(row):
        return row['url'] if 'url' in row else base + quote(row['asset'], safe='-._')
    for m in cfg['models']:
        m['url'] = route(plan['models'][m['name']])
    for r in cfg['runtimes']:
        r['url'] = route(plan['runtimes'][r['variant_id']])
    return cfg


def assemble(staging, out, base, version):
    cfg = configure(staging, base, version)
    plan = verify_staging(staging)
    if plan['parent_sha256'] == PACKAGE_SHA and not version.startswith('0.0.0-fixture.'):
        raise ValueError('superseded Mac CPU parent: use the current Metal release staging; historical fixture assembly only')
    pin, _ = verify_sdk()
    out.mkdir(parents=True, exist_ok=False)
    author = out / 'author'
    shutil.copytree(staging / 'author', author)
    # Generated copy only; the sealed staging and tested parent remain unchanged.
    (author / 'plugin.json').unlink()
    emit(author / 'plugin.json', cfg)
    env = {**os.environ, 'GOTOOLCHAIN': 'local', 'GOWORK': 'off', 'GOPROXY': 'off'}
    binary = out / 'assemble'
    build = subprocess.run(['/usr/local/go1.27/bin/go', 'build', '-trimpath', '-buildvcs=false',
                            '-o', str(binary), str(ROOT / 'scripts/private_cp1_package.go')],
                           cwd=SDK_SOURCE, env=env, capture_output=True, timeout=60)
    put(out / 'build.stdout', build.stdout); put(out / 'build.stderr', build.stderr)
    if build.returncode:
        raise ValueError('SDK assembler build failed; retained logs')
    done = subprocess.run([str(binary), str(author)], capture_output=True, timeout=60)
    put(out / 'assemble.stdout', done.stdout); put(out / 'assemble.stderr', done.stderr)
    if done.returncode:
        raise ValueError('SDK package assembly failed; retained logs')
    result = {'assembly': json.loads(done.stdout), 'sdk_revision': pin['revision'],
              'staging_inventory_sha256': sha(staging / 'staging-inventory.json'),
              'assembler_sha256': sha(ROOT / 'scripts/private_cp1_package.go'),
              'asset_base_url': base_url(base), 'remote_urls_verified': False,
              'signed': False, 'installed': False, 'published': False,
              'human_level_qualified': False, 'public_distribution_ready': False}
    emit(out / 'result.json', result)
    verify_staging(staging)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    s = sub.add_parser('stage'); s.add_argument('--out', type=Path, required=True)
    s.add_argument('--profile', choices=('metal-current','idle-vad-checkpoint','historical-fixture'), required=True)
    a = sub.add_parser('assemble')
    for n in ('staging', 'out'):
        a.add_argument('--' + n, type=Path, required=True)
    for n in ('base-url', 'version'):
        a.add_argument('--' + n, required=True)
    args = p.parse_args()
    if args.action == 'stage':
        if args.profile == 'metal-current':
            from scripts.refresh_desktop_distribution import stage_current
            result = stage_current(args.out.resolve())
        elif args.profile == 'idle-vad-checkpoint':
            from scripts.refresh_desktop_distribution import stage_vad_checkpoint
            result = stage_vad_checkpoint(args.out.resolve())
        else:
            result = stage(args.out.resolve())
        print(json.dumps({'assets': len(result['assets']), 'bytes': sum(r['size'] for r in result['assets'].values()),
                          'notice_files': result['notice_files'], 'public_distribution_ready': False}))
    else:
        print(json.dumps(assemble(args.staging.resolve(), args.out.resolve(), args.base_url, args.version)))
