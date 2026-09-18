"""Rebind an explicitly selected native parent to the current carrier source.

Parent execution evidence is not inherited. Only verified unchanged runtime and
model bytes are reused; changed worker/ASR images and the SDK require new proof.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess

from scripts.native_checkpoint_binding import sha, verify_checkpoint
from scripts.package_native_runtime import bind_carrier, runtime_inventory, safe_relative, verify


def relocate_macos(path, runtime):
    def output(*command):
        return subprocess.check_output(list(map(str, command)), text=True, timeout=30)
    def deps():
        return [line.strip().split(' (compatibility', 1)[0]
                for line in output('/usr/bin/otool', '-L', path).splitlines()[1:]]
    def rpaths():
        return re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset',
                          output('/usr/bin/otool', '-l', path))
    executable = path.parent.name == 'bin'
    flags = [] if executable else ['-id', '@rpath/' + path.name]
    for dep in deps():
        if (not executable and Path(dep).name == path.name) or dep.startswith(('/usr/lib/', '/System/Library/')):
            continue
        if not (runtime / 'lib' / Path(dep).name).is_file():
            raise ValueError('undeclared dependency: ' + dep)
        flags += ['-change', dep, ('@loader_path/../lib/' if executable else '@loader_path/') + Path(dep).name]
    for value in rpaths():
        flags += ['-delete_rpath', value]
    if flags:
        output('/usr/bin/install_name_tool', *flags, path)
    output('/usr/bin/codesign', '--force', '--sign', '-', path)
    output('/usr/bin/codesign', '--verify', '--strict', path)
    prefix = '@loader_path/../lib/' if executable else '@loader_path/'
    if rpaths() or not all(dep.startswith(('/usr/lib/', '/System/Library/', prefix))
                          or (not executable and dep == '@rpath/' + path.name) for dep in deps()):
        raise ValueError('runtime still depends on a build path')


def parent_bytes(parent, freeze_sha):
    if sha(parent / 'freeze.json') != freeze_sha:
        raise ValueError('parent freeze binding differs')
    frozen = json.loads((parent / 'freeze.json').read_text())
    record = json.loads((parent / 'carrier-build.json').read_text())
    profile = verify(parent / 'runtime', frozen['runtime_manifest_sha256'])
    carrier = parent / 'runtime' / ('aii-voice-t3.exe' if profile['platform'] == 'windows' else 'aii-voice-t3')
    if (sha(carrier) != frozen['carrier_sha256'] or sha(carrier) != record['carrier_sha256']
            or record['runtime_manifest_sha256'] != frozen['runtime_manifest_sha256']):
        raise ValueError('parent carrier/runtime binding differs')
    bound = {str(parent / name): sha(parent / name) for name in ('freeze.json', 'carrier-build.json')}
    bound[str(carrier)] = sha(carrier)
    bound[str(parent / 'runtime/voice-runtime.json')] = frozen['runtime_manifest_sha256']
    for name, row in profile['files'].items():
        bound[str(parent / 'runtime' / name)] = row['sha256']
    data = Path(frozen['models_root'])
    for name, row in frozen['models'].items():
        safe_relative(name)
        path = data / name
        if path.is_symlink() or data.is_symlink() or path.stat().st_size != row['bytes'] or sha(path) != row['sha256']:
            raise ValueError('parent model bytes differ: ' + name)
        bound[str(path)] = row['sha256']
    return frozen, profile, bound


def write_current_settings(worker, runtime, out):
    """Declare the replacement worker, never inherit a parent's stale options."""
    raw = subprocess.check_output([str(worker), '--describe-settings'], timeout=30)
    settings = json.loads(raw)
    if not isinstance(settings, list) or not settings:
        raise ValueError('worker settings declaration must be a nonempty list')
    keys = [s.get('key') for s in settings if isinstance(s, dict)]
    if len(keys) != len(settings) or not all(isinstance(k, str) and k for k in keys) or len(set(keys)) != len(keys):
        raise ValueError('worker settings declaration has invalid/duplicate keys')
    data = (json.dumps(settings, indent=2) + '\n').encode()
    (runtime / 'resources/settings.json').write_bytes(data)
    (out / 'settings.json').write_bytes(data)
    return settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('parent', 'worker', 'asr', 'session-library', 'out', 'go'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--parent-sha256', required=True)
    args = parser.parse_args()
    parent, out = args.parent.resolve(), args.out.resolve()
    frozen, profile, bindings = parent_bytes(parent, args.parent_sha256)
    platform = profile['platform']
    suffix = {'darwin': '.dylib', 'linux': '.so', 'windows': '.dll'}[platform]
    worker_name = 'bin/aii_voice_worker' + ('.exe' if platform == 'windows' else '')
    asr_names = [n for n in profile['files'] if Path(n).name in ('libaii_native_asr'+suffix, 'aii_native_asr'+suffix)]
    if len(asr_names) != 1 or worker_name not in profile['files']:
        raise ValueError('parent worker/ASR layout differs')
    replacements = {worker_name: args.worker.resolve(), asr_names[0]: args.asr.resolve()}
    sessions = [n for n in profile['files'] if Path(n).name in
                ('libaii_voice_runtime'+suffix, 'aii_voice_runtime'+suffix)]
    if len(sessions) != 1:
        raise ValueError('parent session library layout differs')
    replacements[sessions[0]] = args.session_library.resolve()
    for path in replacements.values():
        bindings[str(path)] = sha(path)
    bindings[str(Path(__file__).resolve())] = sha(Path(__file__))
    out.mkdir(parents=True, exist_ok=False)
    runtime = out / 'runtime'
    for name in profile['files']:
        target = runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(replacements.get(name, parent / 'runtime' / name), target)
    for name in replacements:
        path = runtime / name
        if platform == 'darwin':
            relocate_macos(path, runtime)
        elif platform == 'linux':
            # Source build already supplies the relocatable runtime rpath.
            dynamic = subprocess.check_output(['readelf', '-d', str(path)], text=True)
            paths = re.findall(r'(?:RPATH|RUNPATH).*?\[(.*?)\]', dynamic)
            expected = '$ORIGIN/../lib' if name.startswith('bin/') else '$ORIGIN'
            # Both resolve to this same runtime/lib for a member in lib/.
            allowed = [expected] if name.startswith('bin/') else ['$ORIGIN', '$ORIGIN/../lib']
            if len(paths) != 1 or paths[0] not in allowed:
                raise ValueError('explicit relocatable rpath required: ' + str(paths))
    if 'resources/settings.json' not in profile['files']:
        raise ValueError('parent lacks runtime settings declaration')
    write_current_settings(runtime / worker_name, runtime, out)
    updated = copy.deepcopy(profile)
    updated['files'] = runtime_inventory(runtime, target_platform=platform)
    updated['qualified'] = False
    delta = {n for n in profile['files'] if profile['files'][n] != updated['files'][n]}
    if not delta or not delta <= set(replacements) | {'resources/settings.json'}:
        raise ValueError('unexpected runtime delta')
    (runtime / 'voice-runtime.json').write_text(json.dumps(updated, indent=2) + '\n')
    binding = sha(runtime / 'voice-runtime.json')
    bind_carrier(runtime, out / 'carrier-build.json', args.go.resolve())
    result = copy.deepcopy(frozen)
    # Prior signing attestations describe the prior PE bytes, not replacements.
    # Leave the parent intact; this candidate needs its own signing ceremony.
    result.pop('authenticode_derivation', None)
    result.update(passed=True, signed=False, installed=False, published=False,
                  runtime_authenticode_verified=False, carrier_authenticode_verified=False,
                  human_level_qualified=False, candidate_execution_validated=False,
                  scope='Integrity-bound candidate only; all execution and installed gates must rerun',
                  parent_checkpoint=str(parent), parent_freeze_sha256=args.parent_sha256,
                  runtime_manifest_sha256=binding, worker_sha256=sha(runtime / worker_name),
                  carrier_sha256=json.loads((out / 'carrier-build.json').read_text())['carrier_sha256'],
                  changed_images=sorted(delta - {'resources/settings.json'}), models_copied=0,
                  settings_changed='resources/settings.json' in delta,
                  settings_sha256=sha(out / 'settings.json'),
                  bindings=bindings)
    for name, source in replacements.items():
        if name != worker_name:
            result['library_hashes'][Path(name).name] = sha(runtime / name)
            result.setdefault('libraries', {})[Path(name).name] = dict(source=str(source),
                source_sha256=sha(source), relocated_sha256=sha(runtime / name))
    for path, digest in bindings.items():
        if sha(path) != digest:
            raise ValueError('input changed during build: ' + path)
    (out / 'freeze.json').write_text(json.dumps(result, indent=2) + '\n')
    verify_checkpoint(out)
    print(json.dumps({k: result[k] for k in ('scope', 'runtime_manifest_sha256', 'carrier_sha256', 'changed_images')}))


if __name__ == '__main__':
    main()
