"""Recover an unsigned integrity checkpoint from a verified runtime stage.

The stage is a byte-bound parent, not evidence that the rebuilt candidate ran.
This is useful when release cleanup retained the exact stage and models but not
the original temporary checkpoint directory. No previous carrier is inherited.
"""
from scripts._assertions import require_assertions
require_assertions()

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

from scripts.native_checkpoint_binding import sha, verify_checkpoint
from scripts.package_native_runtime import bind_carrier, refuse_interpreter_profile, safe_relative, verify
from scripts.stage_qualified_runtime import check_archive


def restore(stage_path, archive, models_root, out, go):
    stage = json.loads(stage_path.read_text())
    if stage.get('passed') is not True or stage.get('models_in_archive') is not False:
        raise ValueError('successful model-external runtime stage required')
    declaration = stage['runtime_archive']
    if sha(archive) != declaration['sha256'] or archive.stat().st_size != declaration['size']:
        raise ValueError('staged archive bytes differ')
    with tarfile.open(archive, 'r:gz') as bundle:
        member = bundle.getmember('runtime/voice-runtime.json')
        manifest = bundle.extractfile(member).read()
    if hashlib.sha256(manifest).hexdigest() != stage['runtime_manifest_sha256']:
        raise ValueError('staged runtime manifest differs')
    profile = json.loads(manifest)
    # Nothing is restored from a stage that describes an interpreter: refused
    # here, before the archive is unpacked and before the output exists.
    refuse_interpreter_profile(profile)
    rows = dict(profile['files'])
    for name in rows:
        safe_relative(name)
    rows['voice-runtime.json'] = dict(sha256=stage['runtime_manifest_sha256'],
                                      bytes=len(manifest), executable=False)
    check_archive(archive, declaration, rows, windows=profile['platform'] == 'windows')
    models_root = models_root.resolve()
    if models_root.is_symlink() or not models_root.is_dir():
        raise ValueError('regular model directory required')
    for name, row in stage['models'].items():
        safe_relative(name)
        path = models_root / name
        if (path.is_symlink() or not path.is_file() or
                not path.resolve().is_relative_to(models_root) or
                path.stat().st_size != row['bytes'] or sha(path) != row['sha256']):
            raise ValueError('staged model differs: ' + name)
    out.mkdir(parents=True, exist_ok=False)
    runtime = out / 'runtime'
    runtime.mkdir()
    with tarfile.open(archive, 'r:gz') as bundle:
        for member in bundle:
            if not member.isfile() or member.name == 'runtime/inventory.json':
                continue
            name = member.name.removeprefix('runtime/')
            target = runtime / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as source, target.open('xb') as destination:
                shutil.copyfileobj(source, destination)
            target.chmod(member.mode)
    if verify(runtime, stage['runtime_manifest_sha256']) != profile:
        raise ValueError('restored runtime differs')
    native_profile = json.loads((runtime / 'native-profile.json').read_text())
    separated = (native_profile.get('models', {}).get('asr') == 'stt' and
                 'stt/diar_classifier/model.onnx' in stage['models'] and
                 'stt/diar_preencode/model.onnx' in stage['models'])
    bind_carrier(runtime, out / 'carrier-build.json', go.resolve())
    record = json.loads((out / 'carrier-build.json').read_text())
    if record['runtime_manifest_sha256'] != stage['runtime_manifest_sha256']:
        raise ValueError('new carrier bound another runtime')
    frozen = dict(passed=True, signed=False, installed=False, published=False,
                  human_level_qualified=False, candidate_execution_validated=False,
                  runtime_authenticode_verified=False, carrier_authenticode_verified=False,
                  scope='Recovered stage bytes and freshly bound carrier; execution and signing not inherited',
                  recovered_stage_sha256=sha(stage_path),
                  recovered_archive_sha256=sha(archive),
                  runtime_manifest_sha256=stage['runtime_manifest_sha256'],
                  carrier_sha256=record['carrier_sha256'],
                  platform=stage['variant_id'].split('-', 1)[0],
                  backend=profile.get('backend', 'native'),
                  models_root=str(models_root), models=stage['models'],
                  model_bytes=sum(row['bytes'] for row in stage['models'].values()),
                  hearing_replaced=separated,
                  library_hashes={Path(name).name: row['sha256'] for name, row in rows.items()
                                  if name.startswith(('lib/', 'bin/')) and
                                  Path(name).suffix in ('.so', '.dylib', '.dll')})
    (out / 'freeze.json').write_text(json.dumps(frozen, indent=2) + '\n')
    verify_checkpoint(out)
    return frozen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('stage', 'archive', 'models-root', 'out', 'go'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = restore(args.stage.resolve(), args.archive.resolve(),
                     args.models_root.resolve(), args.out.resolve(), args.go.resolve())
    print(json.dumps({key: result[key] for key in
                      ('scope', 'runtime_manifest_sha256', 'carrier_sha256', 'model_bytes')}))


if __name__ == '__main__':
    main()
