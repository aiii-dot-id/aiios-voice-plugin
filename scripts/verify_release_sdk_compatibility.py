"""Reassemble unchanged carriers/config through a separately frozen current SDK."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import hashlib

from scripts.prepare_desktop_distribution import census, emit, put, sha


def assembly_receipt(prior):
    # Two existing producer receipt names; never guess between conflicting
    # records or silently accept an unsuccessful build.
    keys = [k for k in ('assembly', 'bundle') if k in prior]
    if prior.get('passed') is not True or len(keys) != 1:
        raise ValueError('one successful assembly receipt required')
    row = prior[keys[0]]
    if not isinstance(row, dict) or not {'bundle', 'sha256'} <= set(row):
        raise ValueError('incomplete assembly receipt')
    return row


def verify(sdk, archive, assembly, out):
    expected = {}
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            if member.isdir():
                continue
            if not member.isfile() or Path(member.name).is_absolute() or '..' in Path(member.name).parts:
                raise ValueError('unexpected SDK archive member')
            raw = tf.extractfile(member).read()
            if member.name in expected:
                raise ValueError('duplicate SDK archive member')
            expected[member.name] = {
                'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    if census(sdk) != expected:
        raise ValueError('SDK source is not exact exported archive')
    out.mkdir(parents=True, exist_ok=False)
    prior = json.loads((assembly / 'result.json').read_text())
    expected_assembly = assembly_receipt(prior)
    if sha(assembly / 'author' / expected_assembly['bundle']) != expected_assembly['sha256']:
        raise ValueError('input package differs from assembly receipt')
    source = Path(__file__).resolve().parents[1] / 'scripts/private_cp1_package.go'
    author = out / 'author'
    shutil.copytree(assembly / 'author', author, ignore=shutil.ignore_patterns('*.aiiospkg', 'stage'))
    env = {**os.environ, 'GOTOOLCHAIN': 'local', 'GOWORK': 'off', 'GOPROXY': 'off'}
    commands = [
        ['/usr/local/go1.27/bin/go', 'test', '-count=1', './...'],
        ['/usr/local/go1.27/bin/go', 'test', '-race', '-count=1', './...'],
        ['/usr/local/go1.27/bin/go', 'build', '-trimpath', '-buildvcs=false', '-o', str(out / 'assemble'), str(source)],
        [str(out / 'assemble'), str(author)],
    ]
    results = []
    for i, command in enumerate(commands):
        run = subprocess.run(command, cwd=sdk, env=env, capture_output=True, timeout=120)
        put(out / f'{i}.stdout', run.stdout); put(out / f'{i}.stderr', run.stderr)
        results.append({'argv': command, 'exit_code': run.returncode,
                        'stdout_sha256': hashlib.sha256(run.stdout).hexdigest(),
                        'stderr_sha256': hashlib.sha256(run.stderr).hexdigest()})
        if run.returncode:
            raise ValueError('current SDK compatibility gate failed; retained output ' + str(i))
    current = json.loads(run.stdout)
    if current != expected_assembly or sha(author / current['bundle']) != expected_assembly['sha256']:
        raise ValueError('current SDK package differs from frozen SDK assembly')
    if census(sdk) != expected:
        raise ValueError('SDK tree changed during validation')
    result = {'passed': True, 'export_sha256': sha(archive), 'sdk_source_files': len(expected),
              'unchanged_carrier_sdk': prior['sdk_revision'], 'assembler_sha256': sha(source),
              'package_sha256': current['sha256'], 'package_byte_identical': True, 'commands': results,
              'scope': 'Uncached current SDK suite and actual package assembly; no new carrier/runtime or installed voice claim'}
    emit(out / 'result.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for name in ('sdk', 'archive', 'assembly', 'out'):
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    verify(a.sdk.resolve(), a.archive.resolve(), a.assembly.resolve(), a.out.resolve())
