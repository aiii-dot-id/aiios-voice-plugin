"""Close every download dependency of an exact desktop integration candidate.

Stage only package-declared release assets and retain pinned upstream evidence.
No signing, publication, installed state or model execution occurs. A complete
local handoff is deliberately not a release-admission verdict.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit

from scripts.prepare_desktop_distribution import copy_asset, emit, put, sha
from scripts.repackage_native_schemas import read_package

REPOSITORY = 'aiii-dot-id/aiios-voice-plugin'


def clean_url(value):
    u = urlsplit(value)
    if (u.scheme != 'https' or not u.hostname or u.username or u.password
            or u.port is not None or u.query or u.fragment
            or '%' in u.path or '\\' in u.path or '..' in u.path.split('/')
            or any(c.isspace() for c in value)):
        raise ValueError('unusable public download URL')
    return u


def release_name(row, version):
    u = clean_url(row['url'])
    prefix = '/' + REPOSITORY + '/releases/download/v' + version + '/'
    if u.hostname != 'github.com' or not u.path.startswith(prefix):
        raise ValueError('foreign repository or release tag')
    name = u.path.removeprefix(prefix)
    if ('/' in name or not name.startswith(row['sha256'] + '-')
            or not re.fullmatch(r'[a-zA-Z0-9_.-]+', name)):
        raise ValueError('release name is not bound to its declared digest')
    return name


def unique(rows, key):
    result = {r[key]: r for r in rows}
    if len(result) != len(rows):
        raise ValueError('duplicate ' + key)
    return result


def dependencies(manifest, files):
    models = json.loads(files['models.json'])
    runtimes = json.loads(files['runtime.json'])['runtimes']
    profiles = json.loads(files['accelerator.json'])
    variants = unique(manifest['variants'], 'variant_id')
    runtime_by_id = unique(runtimes, 'variant_id')
    model_by_name = unique(models, 'name')
    unique(models, 'path')
    if set(variants) != set(runtime_by_id) or set(variants) != set(profiles):
        raise ValueError('runtime/accelerator/variant coverage differs')
    if {v['platform'] for v in variants.values()} != {'macos', 'linux', 'windows'} or len(variants) != 3:
        raise ValueError('three desktop variants required')
    targets = {}; used = set()
    for vid, variant in variants.items():
        selected = profiles[vid]['models']
        if not selected or len(selected) != len(set(selected)) or not set(selected) <= set(model_by_name):
            raise ValueError('invalid platform model selection')
        used.update(selected)
        targets[variant['platform']] = dict(
            variant_id=vid, runtime=runtime_by_id[vid],
            models=[model_by_name[n] for n in selected],
            accelerator=profiles[vid], carrier_sha256=variant['artifact_hash'])
    if used != set(model_by_name):
        raise ValueError('undeployed model in package union')
    for row in models + runtimes:
        if (type(row['size']) is not int or row['size'] <= 0
                or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
            raise ValueError('invalid declared size/digest')
    return models, runtimes, targets


def upstream_rows(models, evidence):
    # Re-use earlier complete anonymous body hashes only when every actual
    # URL/path/size/digest still agrees. Never promote a range or HEAD result.
    if evidence.get('passed') is not True:
        raise ValueError('upstream body proof did not pass')
    proof = unique(evidence['rows'], 'path')
    rows = []
    for m in models:
        u = clean_url(m['url'])
        if u.hostname == 'github.com':
            continue
        if u.hostname not in ('huggingface.co', 'raw.githubusercontent.com') or not re.search('/[0-9a-f]{40}/', u.path):
            raise ValueError('unreviewed or unpinned upstream')
        r = proof.get(m['path'], {})
        if (any(r.get(k) != m[k] for k in ('path', 'url', 'size', 'sha256'))
                or r.get('passed') is not True or r.get('authenticated') is not False
                or r.get('complete_body_hashed') is not True
                or r.get('observed_sha256') != m['sha256']):
            raise ValueError('upstream proof does not bind model: ' + m['path'])
        rows.append({**m, 'previous_complete_anonymous_download_utc': evidence['utc']})
    if set(proof) != {r['path'] for r in rows}:
        raise ValueError('upstream evidence census differs')
    return rows


def platform_plan(targets, setup):
    if set(setup['platforms']) != set(targets) or setup.get('automatic_configuration') is not False:
        raise ValueError('operator setup coverage/authority differs')
    result = {}
    for platform, target in targets.items():
        model_bytes = sum(m['size'] for m in target['models'])
        result[platform] = dict(target, operator_config_merge=setup['platforms'][platform],
                                model_bytes=model_bytes,
                                download_bytes=target['runtime']['size'] + model_bytes,
                                installed_runtime_and_models_bytes=target['runtime']['installed_bytes'] + model_bytes,
                                disk_budget_is_lower_bound_not_free_space_requirement=True)
    return result


def audit_staged(root):
    plan = json.loads((root / 'publication-plan.json').read_text())
    if root.is_symlink() or (root / 'assets').is_symlink() or any(p.is_symlink() for p in (root / 'assets').rglob('*')):
        raise ValueError('symlink in publication handoff')
    expected = {r['file'] for r in plan['assets']}
    if len(expected) != len(plan['assets']):
        raise ValueError('duplicate publication asset')
    actual = {p.relative_to(root).as_posix() for p in (root / 'assets').rglob('*') if p.is_file()}
    if actual != expected:
        raise ValueError('publication file census differs')
    for r in plan['assets']:
        name = PurePosixPath(r['file'])
        if name.is_absolute() or '..' in name.parts or not r['file'].startswith('assets/'):
            raise ValueError('publication path escapes handoff')
        p = root / name
        if p.is_symlink() or not p.is_file() or p.stat().st_size != r['size'] or sha(p) != r['sha256']:
            raise ValueError('publication asset differs: ' + r['file'])
    packages = [r for r in plan['assets'] if r['kind'] == 'plugin']
    if len(packages) != 1 or packages[0]['sha256'] != plan['candidate_sha256']:
        raise ValueError('one exact candidate package required')
    manifest, payloads = read_package(root / packages[0]['file'], plan['candidate_sha256'])
    if (plan.get('schema') != 'aiii-voice-desktop-publication-handoff'
            or plan.get('repository') != REPOSITORY
            or plan.get('release_tag') != 'v' + manifest['version']
            or any(plan.get(k) is not False for k in ('signed', 'published', 'catalog_generated', 'beta_release_ready'))
            or plan.get('final_signed_bytes_required') is not True):
        raise ValueError('publication destination or pre-signing boundary differs')
    models, runtimes, targets = dependencies(manifest, payloads)
    if sha(root / 'upstream-download-evidence.json') != plan['upstream_evidence_sha256']:
        raise ValueError('upstream evidence changed')
    external = upstream_rows(models, json.loads((root / 'upstream-download-evidence.json').read_text()))
    if external != plan['upstream']:
        raise ValueError('upstream plan differs from actual package')
    wanted = {}
    for kind, rows in (('model', models), ('runtime', runtimes)):
        for row in rows:
            if clean_url(row['url']).hostname == 'github.com':
                name = 'assets/' + release_name(row, manifest['version'])
                wanted[name] = dict(kind=kind, file=name, size=row['size'], sha256=row['sha256'], url=row['url'])
    actual_rows = {r['file']: r for r in plan['assets'] if r['kind'] != 'plugin'}
    if actual_rows != wanted:
        raise ValueError('asset plan does not close actual package declarations')
    setup = json.loads((root / 'operator-setup.json').read_text())
    if plan['platforms'] != platform_plan(targets, setup):
        raise ValueError('platform plan/setup differs from actual package')
    return dict(passed=True, assets=len(expected),
                total_asset_bytes=sum(r['size'] for r in plan['assets']),
                candidate_sha256=plan['candidate_sha256'],
                upstream_files=len(plan['upstream']),
                local_dependency_closure=True, published=False,
                catalog_generated=False, beta_release_ready=False)


def stage(candidate, generated, upstream, out):
    receipt = json.loads((candidate / 'result.json').read_text())
    if receipt['passed'] is not True or receipt['signed'] or receipt['beta_release_ready']:
        raise ValueError('this pre-signing handoff expects an unsigned integration candidate')
    assembly = receipt['bundle']; source = candidate / 'author' / assembly['bundle']
    manifest, files = read_package(source, assembly['sha256'])
    models, runtimes, targets = dependencies(manifest, files)
    body_proof = json.loads(upstream.read_text())
    external = upstream_rows(models, body_proof)
    prepared = []
    for kind, rows in (('model', models), ('runtime', runtimes)):
        for row in rows:
            if clean_url(row['url']).hostname != 'github.com':
                if kind != 'model': raise ValueError('runtime must be an owned release asset')
                continue
            name = release_name(row, manifest['version'])
            path = (generated if kind == 'model' else candidate / 'assets') / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size != row['size'] or sha(path) != row['sha256']:
                raise ValueError('declared asset missing or changed: ' + name)
            # GitHub's asset size limit does not apply to the pinned upstream
            # large weights; it does apply to each file we intend to upload.
            if row['size'] >= 2 * 1024**3:
                raise ValueError('owned asset needs a different publication route')
            prepared.append(dict(kind=kind, file='assets/' + name, size=row['size'],
                                 sha256=row['sha256'], url=row['url'], source=str(path)))
    if len({r['file'] for r in prepared}) != len(prepared):
        raise ValueError('colliding release asset names')
    package_row = dict(kind='plugin', file='assets/' + source.name,
                       size=source.stat().st_size, sha256=assembly['sha256'], source=str(source),
                       url='https://github.com/' + REPOSITORY + '/releases/download/v' + manifest['version'] + '/' + source.name)
    prepared.append(package_row)
    out.mkdir(parents=True, exist_ok=False)
    for row in prepared:
        copy_asset(row['source'], out / row['file'], row['size'], row['sha256'])
    published_rows = [{k: v for k, v in r.items() if k != 'source'} for r in prepared]
    setup = json.loads((candidate / 'operator-setup.json').read_text())
    targets = platform_plan(targets, setup)
    index = json.loads(files['notices/INDEX.json'])
    plan = dict(schema='aiii-voice-desktop-publication-handoff',
                utc=datetime.now(timezone.utc).isoformat(), repository=REPOSITORY,
                release_tag='v' + manifest['version'], candidate_sha256=assembly['sha256'],
                candidate_receipt_sha256=sha(candidate / 'result.json'),
                upstream_evidence_sha256=sha(upstream), assets=published_rows,
                upstream=external, platforms=targets,
                distribution_review_complete=index['distribution_review_complete'],
                distribution_open_items=index['open_items'],
                signed=False, published=False, catalog_generated=False,
                final_signed_bytes_required=True, beta_release_ready=False)
    emit(out / 'publication-plan.json', plan)
    put(out / 'SHA256SUMS', ''.join(r['sha256'] + '  ' + r['file'] + '\n' for r in sorted(published_rows, key=lambda r: r['file'])).encode())
    put(out / 'upstream-download-evidence.json', upstream.read_bytes())
    put(out / 'operator-setup.json', (candidate / 'operator-setup.json').read_bytes())
    result = audit_staged(out)
    result.update(source_sha256=sha(Path(__file__)),
                  plan_sha256=sha(out / 'publication-plan.json'))
    emit(out / 'result.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ('candidate', 'generated-assets', 'upstream-evidence', 'out'):
        p.add_argument('--' + n, type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(stage(a.candidate.resolve(), a.generated_assets.resolve(),
                           a.upstream_evidence.resolve(), a.out.resolve())))


if __name__ == '__main__': main()
