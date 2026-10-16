"""Close every download dependency of an exact desktop integration candidate.

Stage only package-declared release assets and retain pinned upstream evidence.
No signing, publication, installed state or model execution occurs. A complete
local handoff is deliberately not a release-admission verdict.
"""
from scripts._assertions import require_assertions
require_assertions()
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit

from scripts.release_files import copy_asset, emit, put, sha
from scripts.repackage_native_schemas import read_package

REPOSITORY = 'aiii-dot-id/aiios-voice-plugin'
# NVIDIA's model terms cover the native-multitalker recognition exports only.
HEARING_NOTICE_GROUP = 'native-multitalker'


def separator_model_path(path):
    """Speech-separator weights in a recognition namespace (stt/ or stt-small/).

    They are derived from a different upstream checkpoint (MossFormer2) and
    can never inherit the NVIDIA hearing terms by sharing a directory.
    """
    namespace, _, rest = path.partition('/')
    return namespace in ('stt', 'stt-small') and any(part.startswith('separator') for part in rest.split('/'))


def release_checksums(rows):
    """Checksum names are flat GitHub release assets, not handoff paths."""
    names = []
    for row in rows:
        path = PurePosixPath(row['file'])
        if len(path.parts) != 2 or path.parts[0] != 'assets' or path.name in ('.', '..'):
            raise ValueError('release asset must be one file beneath assets/')
        names.append(path.name)
    if len(set(names)) != len(names):
        raise ValueError('colliding flat release asset names')
    return ''.join(row['sha256'] + '  ' + PurePosixPath(row['file']).name + '\n'
                   for row in sorted(rows, key=lambda r: r['file'])).encode()


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


def release_assets(rows):
    """Shared declarations use one asset; conflicting declarations still fail."""
    assets = {}
    binding = ('kind', 'file', 'size', 'sha256', 'url')
    for row in rows:
        previous = assets.get(row['file'])
        if previous is not None:
            if any(previous[k] != row[k] for k in binding):
                raise ValueError('colliding release asset names')
        else:
            assets[row['file']] = row
    return list(assets.values())


def model_notice_bindings(models, files):
    """A historical distribution verdict cannot cover different model bytes.

    This checks attribution inventory integrity, not legal clearance. Original
    notices for retired models may remain, but every currently downloaded model
    must have exactly one matching path, digest, size and named notice group.
    """
    try:
        index = json.loads(files['notices/INDEX.json'])
        declared = unique(index['models'], 'path')
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('model notice inventory missing or invalid') from error
    for model in models:
        row = declared.get(model['path'], {})
        if (row.get('sha256') != model['sha256']
                or type(row.get('bytes')) is not int or row['bytes'] != model['size']
                or not isinstance(row.get('notice_group'), str)
                or not row['notice_group'].strip()):
            raise ValueError('model notice binding differs: ' + model['path'])
        group = row['notice_group']
        if separator_model_path(model['path']) and group == HEARING_NOTICE_GROUP:
            raise ValueError('separator weights cannot carry the NVIDIA hearing terms: ' + model['path'])
        if (PurePosixPath(group).is_absolute() or '..' in group.split('/')
                or '\\' in group or group != group.strip()
                or not any(name.startswith('notices/' + group + '/') and raw
                           for name, raw in files.items())):
            raise ValueError('model notice group has no included notice: ' + model['path'])
    if 'uid_replacement' in index:
        # A current replacement statement must describe the actual UID model,
        # not survive from a retired checkpoint under a still-current heading.
        replacement = index['uid_replacement']
        current = next((m for m in models if m['path'] == 'uid/model.onnx'), None)
        if (not isinstance(replacement, dict) or current is None
                or replacement.get('model_sha256') != current['sha256']):
            raise ValueError('UID replacement notice names a different model')
        record = replacement.get('record')
        group = declared['uid/model.onnx']['notice_group']
        if (not isinstance(record, str) or not record.startswith('notices/' + group + '/')
                or '..' in record.split('/') or '\\' in record or record != record.strip()
                or not files.get(record)):
            raise ValueError('UID replacement record is not included in its model notice group')


def conditional_models(models, files):
    """Names of the models that only some values of a setting need.

    Such a model belongs to no component set: every platform acquires it
    while the setting holds one of its values. Its condition must name an
    enum setting this package declares and values that setting has, which
    is what the host that reads the package requires of it.
    """
    named = {m['name'] for m in models if 'when' in m}
    if not named:
        return named
    try:
        settings = unique(json.loads(files['settings.json']), 'key')
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('a conditional model needs the package settings') from error
    for model in models:
        if 'when' not in model:
            continue
        when = model['when']
        if (not isinstance(when, dict) or set(when) != {'setting', 'values'}
                or not isinstance(when['setting'], str)
                or not isinstance(when['values'], list) or not when['values']
                or any(not isinstance(v, str) or not v for v in when['values'])
                or len(set(when['values'])) != len(when['values'])):
            raise ValueError('invalid model condition: ' + model['path'])
        setting = settings.get(when['setting'])
        if (not isinstance(setting, dict) or setting.get('type') != 'enum'
                or not isinstance(setting.get('values'), list)
                or not set(when['values']) <= set(setting['values'])):
            raise ValueError('model condition names no declared setting value: ' + model['path'])
    return named


def conditional_downloads(models):
    """What each value of a setting adds to a download, beyond a component set's own models."""
    result = {}
    for model in models:
        for value in model.get('when', {}).get('values', ()):
            row = result.setdefault(model['when']['setting'] + '=' + value, dict(files=0, bytes=0))
            row['files'] += 1
            row['bytes'] += model['size']
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
    if {v['platform'] for v in variants.values()} != {'macos', 'linux', 'windows'}:
        raise ValueError('variants covering all three desktops required')
    preference = manifest.get('variant_preference')
    if len(variants) != 3 or preference is not None:
        if (not isinstance(preference, list) or any(not isinstance(v, str) for v in preference)
                or len(preference) != len(variants) or len(set(preference)) != len(variants)
                or set(preference) != set(variants)):
            raise ValueError('multi-set publication requires complete variant_preference')
    targets = {}; used = set()
    conditional = conditional_models(models, files)
    for vid, variant in variants.items():
        selected = profiles[vid]['models']
        if not selected or len(selected) != len(set(selected)) or not set(selected) <= set(model_by_name):
            raise ValueError('invalid platform model selection')
        if conditional & set(selected):
            # A set names what its platform always needs; a conditional
            # model is the same on every platform and is in none.
            raise ValueError('a component set names a model that only some values of a setting need')
        used.update(selected)
        targets[vid] = dict(
            platform=variant['platform'], variant_id=vid, runtime=runtime_by_id[vid],
            models=[model_by_name[n] for n in selected],
            accelerator=profiles[vid], carrier_sha256=variant['artifact_hash'])
    if used | conditional != set(model_by_name):
        raise ValueError('undeployed model in package union')
    for row in models + runtimes:
        if (type(row['size']) is not int or row['size'] <= 0
                or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
            raise ValueError('invalid declared size/digest')
        if clean_url(row['url']).hostname not in ('github.com', 'huggingface.co', 'raw.githubusercontent.com'):
            raise ValueError('unreviewed or placeholder download host')
    model_notice_bindings(models, files)
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
    platforms = {target['platform'] for target in targets.values()}
    if set(setup['platforms']) != platforms or setup.get('automatic_configuration') is not False:
        raise ValueError('operator setup coverage/authority differs')
    result = {}
    for variant, target in targets.items():
        model_bytes = sum(m['size'] for m in target['models'])
        result[variant] = dict(target, operator_config_merge=setup['platforms'][target['platform']],
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
    wanted = []
    for kind, rows in (('model', models), ('runtime', runtimes)):
        for row in rows:
            if clean_url(row['url']).hostname == 'github.com':
                name = 'assets/' + release_name(row, manifest['version'])
                wanted.append(dict(kind=kind, file=name, size=row['size'], sha256=row['sha256'], url=row['url']))
    wanted = {r['file']: r for r in release_assets(wanted)}
    actual_rows = {r['file']: r for r in plan['assets'] if r['kind'] != 'plugin'}
    if actual_rows != wanted:
        raise ValueError('asset plan does not close actual package declarations')
    setup = json.loads((root / 'operator-setup.json').read_text())
    if (plan['variants'] != platform_plan(targets, setup)
            or plan.get('variant_preference') != manifest.get('variant_preference')):
        raise ValueError('component plan/setup differs from actual package')
    if plan.get('conditional_downloads', {}) != conditional_downloads(models):
        raise ValueError('conditional downloads differ from actual package')
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
    prepared = release_assets(prepared)
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
                upstream=external, variants=targets, variant_preference=manifest.get('variant_preference'),
                conditional_downloads=conditional_downloads(models),
                distribution_review_complete=index['distribution_review_complete'],
                distribution_open_items=index['open_items'],
                signed=False, published=False, catalog_generated=False,
                final_signed_bytes_required=True, beta_release_ready=False)
    emit(out / 'publication-plan.json', plan)
    put(out / 'SHA256SUMS', release_checksums(published_rows))
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
