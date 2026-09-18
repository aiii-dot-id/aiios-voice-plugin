"""Prepare one catalog replacement from a signed handoff; never sign or publish.

The caller must first verify the existing catalog with AII OS. Its exact digest
is the concurrency fence: a changed catalog requires a fresh review, not an
overwrite. The actual host separately verifies the prepared catalog after
signing. No SDK, package, or catalog authority is reimplemented here.
"""
import argparse
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from scripts.prepare_desktop_distribution import sha
from scripts.repackage_native_schemas import read_package
from scripts.verify_publication_catalog import check_catalog

DETAIL_KEYS = {'title', 'description', 'publisher', 'homepage', 'license',
               'category', 'keywords'}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=True) + '\n').encode()


def render_update(raw, expected_sha256, entry, details, generated):
    if digest(raw) != expected_sha256:
        raise ValueError('catalog changed; re-read and verify its current signed bytes')
    if set(details) - DETAIL_KEYS:
        raise ValueError('catalog details cannot override package or compatibility fields')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', generated):
        raise ValueError('generated must be an explicit UTC RFC3339 timestamp')
    datetime.fromisoformat(generated.replace('Z', '+00:00'))
    text = raw.decode('utf-8')
    if text.count('```json\n') != 1:
        raise ValueError('expected exactly one catalog JSON block')
    prefix, block = text.split('```json\n', 1)
    if '```' not in block:
        raise ValueError('unterminated catalog JSON block')
    encoded, suffix = block.split('```', 1)
    catalog = json.loads(encoded)
    plugins = catalog['plugins']
    ids = [row['id'] for row in plugins]
    if len(ids) != len(set(ids)) or ids.count(entry['id']) != 1:
        raise ValueError('replacement needs one existing plugin, with no duplicate ids')
    updated = copy.deepcopy(catalog)
    updated['plugins'][ids.index(entry['id'])] = {**entry, **details}
    updated['generated'] = generated
    if any(key in entry for key in ('aiios_min_version', 'aiios_max_exclusive_version')):
        features = updated.setdefault('must_understand', [])
        if not isinstance(features, list) or any(not isinstance(x, str) for x in features):
            raise ValueError('invalid must_understand list')
        if 'compat' not in features:
            features.append('compat')
    result = (prefix + '```json\n' + json_bytes(updated).decode() + '```' + suffix).encode()
    payload = dict(catalog_version=updated['catalog_version'], generated=generated,
                   catalog_sha256='sha256:' + digest(result))
    return result, payload


def prepare(catalog, expected_sha256, handoff, details_file, generated, out):
    raw = catalog.read_bytes()
    plan = json.loads((handoff / 'publication-plan.json').read_text())
    entry = json.loads((handoff / 'catalog-entry.json').read_text())
    if sha(handoff / 'catalog-entry.json') != plan['catalog_entry_sha256']:
        raise ValueError('handoff catalog entry changed')
    plugins = [row for row in plan['assets'] if row['kind'] == 'plugin']
    if len(plugins) != 1:
        raise ValueError('one unified plugin archive required')
    row = plugins[0]
    name = Path(row['file'])
    if name.is_absolute() or '..' in name.parts:
        raise ValueError('unsafe handoff asset path')
    archive = handoff / name
    if archive.is_symlink() or not archive.is_file():
        raise ValueError('handoff package must be a regular file')
    if sha(archive) != row['sha256'] or archive.stat().st_size != row['size']:
        raise ValueError('signed package bytes changed')
    if row['sha256'] != plan['signed_package_sha256']:
        raise ValueError('handoff names a different signed package')
    verdict_path = handoff / 'host-verification.json'
    verdict = json.loads(verdict_path.read_text())
    if (sha(verdict_path) != plan['host_verification_sha256']
            or verdict.get('passed') is not True or verdict.get('tier') != 'T3'
            or verdict.get('host_vcs_modified') is not False
            or verdict.get('signed_package_sha256') != row['sha256']
            or verdict.get('tampered_archive_rejected') is not True):
        raise ValueError('exact clean-host signature/tamper evidence required')
    manifest, _ = read_package(archive, row['sha256'], platform_signature=True)
    check_catalog(entry, manifest, row['sha256'], row['size'], row['url'])
    result, payload = render_update(raw, expected_sha256, entry,
                                   json.loads(details_file.read_text()), generated)
    # An existing output can contain a signature for other bytes. Never reuse it.
    out.mkdir(parents=True, exist_ok=False)
    (out / 'aiios-plugins.md').write_bytes(result)
    (out / 'aiios-plugins.sig-payload.json').write_bytes(json_bytes(payload))
    report = dict(prepared=True, signed=False, published=False,
                  previous_catalog_sha256=expected_sha256,
                  catalog_sha256=digest(result), package_sha256=row['sha256'],
                  version=entry['version'], details_sha256=sha(details_file))
    (out / 'preparation.json').write_bytes(json_bytes(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('catalog', 'handoff', 'details', 'out'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--catalog-sha256', required=True)
    parser.add_argument('--generated', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.catalog, args.catalog_sha256, args.handoff,
                             args.details, args.generated, args.out)))


if __name__ == '__main__':
    main()
