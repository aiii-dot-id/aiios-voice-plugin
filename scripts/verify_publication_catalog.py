"""Test catalog generation against actual unsigned bytes; publish nothing.

Outputs are validation fixtures, NOT a release catalog or a T3 admission.
The final catalog must be regenerated from the operator-signed archive.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from scripts.prepare_desktop_distribution import emit, put, sha
from scripts.repackage_native_schemas import read_package
from scripts.stage_desktop_publication import REPOSITORY
from scripts.verify_release_sdk_compatibility import assembly_receipt


def check_catalog(value, manifest, package_hash, package_size, url):
    if (value.get('id') != manifest['id'] or value.get('version') != manifest['version']
            or value.get('tier') != 'T3'):
        raise ValueError('catalog identity/version/tier differs from archive')
    if value.get('summary') != (manifest.get('title') or manifest.get('description', '')):
        raise ValueError('catalog summary differs from archive')
    wanted = {(v['platform'], v['arch']) for v in manifest['variants']}
    rows = value.get('packages', [])
    if len(rows) != len(wanted) or {(r['platform'], r['arch']) for r in rows} != wanted:
        raise ValueError('catalog platform coverage differs from archive')
    if any((r['url'], r['sha256'], r['size']) != (url, 'sha256:' + package_hash, package_size) for r in rows):
        raise ValueError('catalog points at different package bytes')


def verify(sdk_tool, candidate, out):
    prior = json.loads((candidate / 'result.json').read_text())
    built = assembly_receipt(prior)
    archive = candidate / 'author' / built['bundle']
    manifest, _ = read_package(archive, built['sha256'])
    config = json.loads((candidate / 'author/plugin.json').read_text())
    url = f'https://github.com/{REPOSITORY}/releases/download/v{manifest["version"]}/{archive.name}'
    tool_hash = sha(sdk_tool)
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    cases = ('actual', 'unpackaged-title', 'stale-version', 'missing-platform', 'damaged-archive')
    for name in cases:
        folder = out / name; folder.mkdir()
        cfg = json.loads(json.dumps(config))
        package = archive
        if name == 'stale-version': cfg['version'] = '9.9.9'
        if name == 'unpackaged-title': cfg['title'] = 'Unpackaged title must never enter the catalog'
        if name == 'missing-platform': cfg['variants'].pop()
        if name == 'damaged-archive':
            package = folder / 'damaged.aiiospkg'
            put(package, archive.read_bytes()[:-1])
        emit(folder / 'plugin.json', cfg)
        cmd = [str(sdk_tool), 'publish', '-tier', 'T3', '-pkg', str(package), '-url', url]
        p = subprocess.run(cmd, cwd=folder, env=os.environ.copy(), capture_output=True, timeout=30)
        put(folder / 'catalog-fixture.stdout', p.stdout)
        put(folder / 'catalog-fixture.stderr', p.stderr)
        if name in ('actual', 'unpackaged-title'):
            if p.returncode != 0: raise ValueError('valid archive refused: ' + p.stderr.decode())
            check_catalog(json.loads(p.stdout), manifest, built['sha256'], archive.stat().st_size, url)
        elif p.returncode != 1 or p.stdout or not p.stderr:
            raise ValueError('invalid catalog input was not refused: ' + name)
        rows.append(dict(case=name, exit_code=p.returncode, stdout_sha256=sha(folder / 'catalog-fixture.stdout'),
                         stderr_sha256=sha(folder / 'catalog-fixture.stderr')))
    if sha(archive) != built['sha256'] or sha(sdk_tool) != tool_hash:
        raise ValueError('catalog input changed during proof')
    report = dict(passed=True, utc=datetime.now(timezone.utc).isoformat(),
                  package_sha256=built['sha256'], tool_sha256=tool_hash, rows=rows,
                  platform_rows=len(manifest['variants']), distinct_archives=1, validation_fixture_only=True,
                  signed=False, published=False, beta_release_ready=False)
    emit(out / 'result.json', report)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('sdk-tool', 'candidate', 'out'): p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(verify(a.sdk_tool.resolve(), a.candidate.resolve(), a.out.resolve())))
