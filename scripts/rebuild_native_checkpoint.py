"""Rebind an explicitly selected native parent to the current carrier source.

Parent execution evidence is not inherited. Only verified unchanged runtime and
model bytes are reused; every changed native image requires new proof.
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


def verified_parent_models(frozen, bound, model_root=None):
    """Relocation is allowed; every original model byte remains mandatory."""
    data = Path(model_root) if model_root is not None else Path(frozen['models_root'])
    if data.is_symlink() or not data.is_dir():
        raise ValueError('regular parent model directory required')
    for name, row in frozen['models'].items():
        safe_relative(name)
        path = data / name
        if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(data.resolve())
                or path.stat().st_size != row['bytes'] or sha(path) != row['sha256']):
            raise ValueError('parent model bytes differ: ' + name)
        bound[str(path)] = row['sha256']
    frozen['models_root'] = str(data.resolve())


def parent_bytes(parent, freeze_sha, model_root=None):
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
    verified_parent_models(frozen, bound, model_root)
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


def optional_libraries(profile, *, uid_frontend=None, uid=None, tts=None):
    """Replace only explicitly selected, already declared native components."""
    suffix = {'darwin': '.dylib', 'linux': '.so', 'windows': '.dll'}[profile['platform']]
    selected = {}
    for stem, source in (('aiii_uid_frontend', uid_frontend),
                         ('aii_native_uid', uid), ('native_pocket_resident', tts)):
        if source is None:
            continue
        source = Path(source)
        if source.is_symlink() or not source.is_file():
            raise ValueError('replacement must be an explicit regular library file')
        matches = [name for name in profile['files']
                   if Path(name).name in (stem+suffix, 'lib'+stem+suffix)]
        if len(matches) != 1:
            raise ValueError('parent native library layout differs: '+stem)
        safe_relative(matches[0])
        selected[matches[0]] = source.resolve()
    return selected


def replace_hearing_models(frozen, runtime, graphs, frontend, out, bindings):
    """Explicit native hearing replacement; preserve all other model bytes."""
    from scripts.prove_native_multitalker import verify_graphs
    graph_record = verify_graphs(graphs)
    if not graph_record.get('compaction', {}).get('graphs'):
        raise ValueError('download-layout hearing graphs required')
    feature_record = json.loads((frontend / 'result.json').read_text())
    mel = frontend / 'mel.f32'
    if (mel.is_symlink() or mel.stat().st_size != 128*257*4
            or sha(mel) != feature_record['files']['mel.f32']):
        raise ValueError('hearing frontend binding differs')
    native = json.loads((runtime / 'native-profile.json').read_text())
    if native['models']['asr'] != 'stt' or native['models']['asr_mel'] != 'stt/mel.f32':
        raise ValueError('explicit stt model layout required')
    selected = {name: Path(frozen['models_root']) / name for name in frozen['models']
                if not name.startswith('stt/')}
    selected.update({'stt/' + name: graphs / name for name in graph_record['artifacts']})
    if 'stt/mel.f32' in selected:
        raise ValueError('graph set must not override bound frontend')
    selected['stt/mel.f32'] = mel
    # The SDK owns these bounds; changing models does not waive admission.
    if len(selected) > 128:
        raise ValueError('hearing model inventory exceeds accelerator profile bound')
    target = out / 'data'
    target.mkdir(exist_ok=False)
    rows = {}
    for name, source in selected.items():
        safe_relative(name)
        if source.is_symlink() or not source.is_file():
            raise ValueError('regular model source required')
        binding = sha(source)
        bindings[str(source)] = binding
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha(destination) != binding:
            raise ValueError('model changed during copy')
        rows[name] = dict(sha256=binding, bytes=destination.stat().st_size)
    for path in (graphs / 'result.json', frontend / 'result.json'):
        bindings[str(path)] = sha(path)
    frozen.update(models_root=str(target), models=rows,
                  model_bytes=sum(row['bytes'] for row in rows.values()),
                  models_copied=len(rows), hearing_replaced=True,
                  hearing_inventory_sha256=sha(graphs / 'result.json'),
                  hearing_frontend_sha256=sha(frontend / 'result.json'))


def select_hearing_execution(runtime, execution):
    """Never carry a previous recognizer's placement promise into new graphs."""
    path = runtime/'native-profile.json'
    profile = json.loads(path.read_text())
    if execution not in (None, 'cpu'):
        raise ValueError('unsupported new hearing execution selection')
    if profile.get('asr_execution') and execution is None:
        raise ValueError('explicit hearing execution required; previous model placement is not inherited')
    if execution == 'cpu' and 'asr_execution' in profile:
        del profile['asr_execution']
        path.write_text(json.dumps(profile, indent=2)+'\n')
    return dict(provider='cpu', qualification_inherited=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('parent', 'worker', 'asr', 'session-library', 'out', 'go'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--parent-sha256', required=True)
    parser.add_argument('--parent-models-root', type=Path,
                        help='Explicit relocated parent models; every original size/hash must still match')
    for name in ('uid-frontend', 'uid', 'tts'):
        parser.add_argument('--'+name, type=Path,
                            help='Explicit replacement for an existing declared native library; fresh qualification required')
    parser.add_argument('--hearing-graphs', type=Path,
                        help='Explicit byte-verified native multi-speaker model replacement')
    parser.add_argument('--hearing-frontend', type=Path,
                        help='Pinned native frontend paired with --hearing-graphs')
    parser.add_argument('--hearing-execution', choices=('cpu',),
                        help='Explicit new recognizer placement; does not change TTS or inherit GPU qualification')
    args = parser.parse_args()
    if (args.hearing_graphs is None) != (args.hearing_frontend is None):
        parser.error('hearing graphs and frontend must be selected together')
    if args.hearing_execution and args.hearing_graphs is None:
        parser.error('hearing execution requires explicit hearing model replacement')
    parent, out = args.parent.resolve(), args.out.resolve()
    frozen, profile, bindings = parent_bytes(parent, args.parent_sha256, args.parent_models_root)
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
    replacements.update(optional_libraries(profile, uid_frontend=args.uid_frontend,
                                          uid=args.uid, tts=args.tts))
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
    added_notices = set()
    if args.hearing_graphs is not None:
        hearing_execution = select_hearing_execution(runtime, args.hearing_execution)
        root = Path(__file__).resolve().parents[1]
        for source in (root / 'runtime/native_multitalker/NOTICE', root / 'LICENSE'):
            name = 'resources/notices/native-multitalker/' + source.name
            destination = runtime / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            bindings[str(source)] = sha(source)
            shutil.copy2(source, destination)
            added_notices.add(name)
    updated = copy.deepcopy(profile)
    updated['files'] = runtime_inventory(runtime, target_platform=platform)
    updated['qualified'] = False
    delta = {n for n in set(profile['files']) | set(updated['files'])
             if profile['files'].get(n) != updated['files'].get(n)}
    allowed = set(replacements) | {'resources/settings.json'} | added_notices
    if args.hearing_graphs is not None:
        allowed.add('native-profile.json')
    if not delta or not delta <= allowed:
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
                  changed_images=sorted(n for n in delta if n in replacements), models_copied=0,
                  execution_profile_changed='native-profile.json' in delta,
                  settings_changed='resources/settings.json' in delta,
                  settings_sha256=sha(out / 'settings.json'),
                  bindings=bindings)
    for name, source in replacements.items():
        if name != worker_name:
            result['library_hashes'][Path(name).name] = sha(runtime / name)
            result.setdefault('libraries', {})[Path(name).name] = dict(source=str(source),
                source_sha256=sha(source), relocated_sha256=sha(runtime / name))
    if args.hearing_graphs is not None:
        result['hearing_execution'] = hearing_execution
        replace_hearing_models(result, runtime, args.hearing_graphs.resolve(),
                               args.hearing_frontend.resolve(), out, bindings)
    for path, digest in bindings.items():
        if sha(path) != digest:
            raise ValueError('input changed during build: ' + path)
    (out / 'freeze.json').write_text(json.dumps(result, indent=2) + '\n')
    verify_checkpoint(out)
    print(json.dumps({k: result[k] for k in ('scope', 'runtime_manifest_sha256', 'carrier_sha256', 'changed_images')}))


if __name__ == '__main__':
    main()
