"""Rebind an explicitly selected native parent to the current carrier source.

Parent execution evidence is not inherited. Only verified unchanged runtime and
model bytes are reused; every changed native image requires new proof.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

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
    env = dict(os.environ)
    # ELF normally delays resolving function imports until first use. An old
    # component can therefore pass --describe-settings and crash on recovery.
    if sys.platform.startswith('linux'):
        env['LD_BIND_NOW'] = '1'
    raw = subprocess.check_output([str(worker), '--describe-settings'], timeout=30, env=env)
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


def optional_libraries(profile, *, uid_frontend=None, uid=None, tts=None, endpoint=None):
    """Replace only explicitly selected, already declared native components."""
    suffix = {'darwin': '.dylib', 'linux': '.so', 'windows': '.dll'}[profile['platform']]
    selected = {}
    for stem, source in (('aiii_uid_frontend', uid_frontend),
                         ('aii_native_uid', uid), ('native_pocket_resident', tts),
                         ('aii_native_endpoint', endpoint)):
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


# The NeMo-Speech.cpp diarizer/ASR library is one set per platform: the
# implementation and its C API, built together from one patched checkout.
NEMO_LIBRARIES = {
    'darwin': ('lib/libnemo_speech_asr.dylib', 'lib/libnemo_speech_asr_c.1.dylib'),
    'linux': ('lib/libnemo_speech_asr.so', 'lib/libnemo_speech_asr_c.so.1'),
    'windows': ('bin/nemo_speech_asr.dll', 'bin/nemo_speech_asr_c.dll'),
}
NEMO_IMAGE = re.compile(r'(?:lib)?nemo_speech_asr(?:_c)?(?:\..*)?')


# NeMo's ggml is one set, built together from one checkout and options
# (macOS: the Apple-silicon baseline CPU and precompiled Metal kernels).
GGML_LIBRARIES = {
    'darwin': ('lib/libggml.0.dylib', 'lib/libggml-base.0.dylib', 'lib/libggml-blas.0.dylib',
               'lib/libggml-cpu.0.dylib', 'lib/libggml-metal.0.dylib'),
}
GGML_IMAGE = re.compile(r'libggml(?:-[a-z]+)?\.0\.dylib')

# Metal kernels compiled at build time. A contained engine cannot write Metal's
# per-user shader cache, and Metal uses that cache only when it can write it,
# so a library that embeds its shader source compiles it again on every start
# (30-40 s on a busy GPU). Each image built with GGML_METAL_EMBED_LIBRARY=OFF
# reads its own kernels: NeMo's stock ggml from beside the executable, the
# Pocket library (metal-library-beside.patch) from beside itself, by its name.
METAL_KERNELS = {
    'lib/libggml-metal.0.dylib': ('bin/default.metallib', None),
    'lib/libnative_pocket_resident.dylib': ('lib/libnative_pocket_resident.metallib',
                                            b'the image that carries ggml-metal could not be located'),
}
EMBEDDED_METAL_SOURCE = b'kernel void kernel_'


def metal_kernels(profile, replacements, metallibs):
    """The compiled kernels each replaced Metal image reads, as {runtime name: source}.

    A library and its kernels are built together: an image without shader
    source requires its metallib, and a metallib is accepted only beside the
    image that reads it, replaced in the same rebuild.
    """
    targets = {Path(kernels).name: (image, kernels) for image, (kernels, _) in METAL_KERNELS.items()}
    given = {}
    for source in map(Path, metallibs or ()):
        if source.name not in targets:
            raise ValueError('no Metal image reads ' + source.name + '; expected ' + ', '.join(sorted(targets)))
        image, kernels = targets[source.name]
        if kernels in given:
            raise ValueError('metallib named twice: ' + source.name)
        if source.is_symlink() or not source.is_file():
            raise ValueError('replacement must be an explicit regular file: ' + source.name)
        if source.read_bytes()[:4] != b'MTLB':
            raise ValueError(source.name + ' is not a compiled Metal library')
        given[kernels] = source.resolve()
    selected = {}
    for image, (kernels, loader) in METAL_KERNELS.items():
        source = given.get(kernels)
        if image not in replacements:
            if source is not None:
                raise ValueError(kernels + ' is replaced only together with ' + image)
            continue
        data = Path(replacements[image]).read_bytes()
        if EMBEDDED_METAL_SOURCE in data:
            if source is not None or kernels in profile['files']:
                raise ValueError(image + ' embeds its shader source and never reads ' + kernels)
            continue
        if source is None:
            raise ValueError(image + ' carries no shader source; its ' + kernels + ' is required')
        if loader is not None and loader not in data:
            raise ValueError(image + ' does not load ' + kernels + '; metal-library-beside.patch required')
        selected[kernels] = source
    return selected


def nemo_libraries(profile, sources):
    """Replace the parent's declared NeMo library set whole, by exact file name."""
    return library_set(profile, sources, NEMO_LIBRARIES, NEMO_IMAGE, 'NeMo')


def ggml_libraries(profile, sources):
    """Replace the parent's declared ggml library set whole, by exact file name."""
    return library_set(profile, sources, GGML_LIBRARIES, GGML_IMAGE, 'ggml')


def library_set(profile, sources, libraries, image, label):
    """Every file of the set is named explicitly; a partial set, a name outside
    the platform's set or a set the parent does not declare exactly is refused.
    """
    if not sources:
        return {}
    if profile['platform'] not in libraries:
        raise ValueError(label + ' library set replacement is declared for ' + ', '.join(sorted(libraries)) + ' only')
    expected = {Path(name).name: name for name in libraries[profile['platform']]}
    selected = {}
    for source in map(Path, sources):
        if source.is_symlink() or not source.is_file():
            raise ValueError('replacement must be an explicit regular library file')
        name = expected.get(source.name)
        if name is None:
            raise ValueError('unknown ' + label + ' library file: ' + source.name
                             + '; expected ' + ', '.join(sorted(expected)))
        if name in selected:
            raise ValueError(label + ' library named twice: ' + source.name)
        selected[name] = source.resolve()
    if set(selected) != set(expected.values()):
        raise ValueError(label + ' library set is replaced whole; missing '
                         + ', '.join(sorted(set(expected.values()) - set(selected))))
    declared = {name for name in profile['files'] if image.fullmatch(Path(name).name)}
    if declared != set(selected):
        raise ValueError('parent does not declare exactly this ' + label + ' library set: ' + ', '.join(sorted(declared)))
    for name in selected:
        safe_relative(name)
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


def add_voice_presets(frozen, runtime, presets, declared, out, bindings):
    """Add the voice presets a replacement worker declares and the parent lacks.

    After the addition the inventory's presets and the worker's declared
    voices are the same set: a declared voice without its preset would be
    refused at every session that chose it, and an undeclared preset is bytes
    no setting can reach. Every parent model is carried unchanged."""
    presets = Path(presets)
    if presets.is_symlink() or not presets.is_dir():
        raise ValueError('regular voice preset directory required')
    native = json.loads((runtime / 'native-profile.json').read_text())
    bank = native['models'].get('tts')
    if not isinstance(bank, str) or not bank:
        raise ValueError('runtime profile names no speech model directory')
    bank += '/embeddings/'
    added = {}
    for source in sorted(presets.iterdir()):
        if (source.is_symlink() or not source.is_file() or source.suffix != '.safetensors'
                or not source.stat().st_size):
            raise ValueError('voice preset must be a regular nonempty .safetensors file: ' + source.name)
        name = bank + source.name
        safe_relative(name)
        if name in frozen['models']:
            raise ValueError('voice preset is already in the parent inventory: ' + source.stem)
        added[name] = source
    if not added:
        raise ValueError('no voice preset to add')
    held = {Path(name).stem for name in frozen['models'] if name.startswith(bank)}
    voices = held | {source.stem for source in added.values()}
    if not isinstance(declared, list) or len(set(declared)) != len(declared) or voices != set(declared):
        raise ValueError('voice presets differ from the declared voices: missing '
                         + ','.join(sorted(set(declared) - voices)) + '; undeclared '
                         + ','.join(sorted(voices - set(declared))))
    selected = {name: Path(frozen['models_root']) / name for name in frozen['models']}
    selected.update(added)
    # The SDK owns this bound; adding voices does not waive admission.
    if len(selected) > 128:
        raise ValueError('model inventory exceeds accelerator profile bound')
    target = out / 'data'
    target.mkdir(exist_ok=False)
    rows = {}
    for name, source in selected.items():
        if source.is_symlink() or not source.is_file():
            raise ValueError('regular model source required')
        binding = sha(source)
        if name in frozen['models'] and binding != frozen['models'][name]['sha256']:
            raise ValueError('parent model bytes differ: ' + name)
        bindings[str(source)] = binding
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha(destination) != binding:
            raise ValueError('model changed during copy')
        rows[name] = dict(sha256=binding, bytes=destination.stat().st_size)
    frozen.update(models_root=str(target), models=rows,
                  model_bytes=sum(row['bytes'] for row in rows.values()),
                  models_copied=len(rows),
                  voice_presets_added=sorted(source.stem for source in added.values()))


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


def ecapa_contract():
    path = Path(__file__).resolve().parents[1] / 'configs/uid-ecapa-binding.json'
    contract = json.loads(path.read_text())
    binding = hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return path, contract, binding


def validate_uid_replacement(model, policy, policy_sha256):
    """Bind the exact supported graph and the explicitly selected calibration."""
    for path in (model, policy):
        if path.is_symlink() or not path.is_file():
            raise ValueError('regular UID model and policy files required')
    contract_path, contract, binding = ecapa_contract()
    if model.stat().st_size != contract['model_bytes'] or sha(model) != contract['model_sha256']:
        raise ValueError('UID model differs from exact native ECAPA contract')
    if not 0 < policy.stat().st_size <= 4096 or sha(policy) != policy_sha256:
        raise ValueError('UID policy byte binding differs')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate UID policy key')
            result[key] = value
        return result
    doc = json.loads(policy.read_text(), object_pairs_hook=unique)
    expected = {'embedding_binding', 'calibration_sha256', 'threshold',
                'minimum_margin', 'minimum_enrollment_samples'}
    if not isinstance(doc, dict) or set(doc) != expected or doc['embedding_binding'] != binding:
        raise ValueError('UID policy model binding or fields differ')
    if not isinstance(doc['calibration_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', doc['calibration_sha256']):
        raise ValueError('explicit UID calibration digest required')
    if type(doc['minimum_enrollment_samples']) is not int or not 1 <= doc['minimum_enrollment_samples'] <= 8:
        raise ValueError('UID enrollment minimum outside native bounds')
    for key, low, high in (('threshold', -1, 1), ('minimum_margin', 0, 2)):
        value = doc[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('UID decision threshold outside native bounds')
    return contract_path, binding


def uid_notice_sources():
    root = Path(__file__).resolve().parents[1] / 'runtime/native_uid_ecapa/notices'
    expected = {
        'SpeechBrain-LICENSE': 'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4',
        'PocketFFT-LICENSE': 'a85ca13fdf90160b64a0698215868c13b74d835ad0a4e2ba44713b8c5058a056',
        'PocketFFT-HEADER-LICENSE': '282fd01ac9ff320b0b93bdbb76c58ef4c06fa93bac21b5deef9179c9e515d337',
        'MODEL-CARD.md': '00f58c3cbd7a7510de9374080da0e82a4c4e8f4df567f7338fe6efe108be705a',
    }
    sources = {name: root / name for name in (*expected, 'NOTICE')}
    for name, path in sources.items():
        if path.is_symlink() or not path.is_file() or not path.stat().st_size:
            raise ValueError('required UID notice unavailable: ' + name)
        if name in expected and sha(path) != expected[name]:
            raise ValueError('upstream UID notice binding differs: ' + name)
    return sources


def replace_uid_models(frozen, runtime, model, policy, policy_sha256, out, bindings):
    """Candidate-only replacement; never migrates installed user profiles."""
    contract_path, binding = validate_uid_replacement(model, policy, policy_sha256)
    _, contract, checked_binding = ecapa_contract()
    if checked_binding != binding:
        raise ValueError('UID contract changed during preparation')
    notices = uid_notice_sources()
    native_path = runtime / 'native-profile.json'
    native = json.loads(native_path.read_text())
    if native['models'].get('uid') != 'uid/model.onnx' or native.get('uid_policy') != 'resources/uid-policy.json':
        raise ValueError('explicit UID model/policy layout required')
    if 'uid/model.onnx' not in frozen['models']:
        raise ValueError('parent lacks declared UID model')
    target = out / 'data'
    target.mkdir(exist_ok=True)
    rows = {}
    for name in frozen['models']:
        safe_relative(name)
        source = model if name == 'uid/model.onnx' else Path(frozen['models_root']) / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('regular model source required')
        digest = sha(source)
        if name == 'uid/model.onnx' and (digest != contract['model_sha256'] or source.stat().st_size != contract['model_bytes']):
            raise ValueError('UID model changed during preparation')
        if name != 'uid/model.onnx' and (digest != frozen['models'][name]['sha256'] or source.stat().st_size != frozen['models'][name]['bytes']):
            raise ValueError('unchanged model binding differs: ' + name)
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            bindings[str(source)] = digest
            shutil.copy2(source, destination)
        if sha(destination) != digest:
            raise ValueError('model changed during UID copy')
        rows[name] = dict(sha256=digest, bytes=destination.stat().st_size)
    bindings[str(policy)] = policy_sha256
    bindings[str(contract_path)] = sha(contract_path)
    shutil.copy2(policy, runtime / native['uid_policy'])
    if sha(runtime / native['uid_policy']) != policy_sha256:
        raise ValueError('UID policy changed during copy')
    # This field supports only same-model guided enrollment, not 256 -> 192.
    native.pop('uid_previous_policy', None)
    native_path.write_text(json.dumps(native, indent=2) + '\n')
    frozen.update(models_root=str(target), models=rows, models_copied=len(rows),
                  model_bytes=sum(row['bytes'] for row in rows.values()), uid_replaced=True,
                  uid_embedding_binding=binding, uid_policy_sha256=policy_sha256,
                  installed_profile_transition_validated=False)
    added = set()
    for name, source in notices.items():
        member = 'resources/notices/ecapa-uid/' + name
        destination = runtime / member
        destination.parent.mkdir(parents=True, exist_ok=True)
        bindings[str(source)] = sha(source)
        shutil.copy2(source, destination)
        if sha(destination) != bindings[str(source)]:
            raise ValueError('UID notice changed during staging')
        added.add(member)
    return added


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('parent', 'worker', 'asr', 'session-library', 'out', 'go'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--parent-sha256', required=True)
    parser.add_argument('--parent-models-root', type=Path,
                        help='Explicit relocated parent models; every original size/hash must still match')
    for name in ('uid-frontend', 'uid', 'tts', 'endpoint'):
        parser.add_argument('--'+name, type=Path,
                            help='Explicit replacement for an existing declared native library; fresh qualification required')
    parser.add_argument('--ggml', type=Path, action='append', metavar='FILE',
                        help='macOS: explicit replacement for the declared ggml library set; repeat to name '
                             'every file of its set; fresh qualification required')
    parser.add_argument('--metallib', type=Path, action='append', metavar='FILE',
                        help='macOS: compiled Metal kernels of a replaced image built without shader source '
                             '(default.metallib for ggml, libnative_pocket_resident.metallib for --tts)')
    parser.add_argument('--nemo', type=Path, action='append', metavar='FILE',
                        help='Explicit replacement for the declared NeMo diarizer/ASR library; repeat to name '
                             'every file of its set; fresh qualification required')
    parser.add_argument('--hearing-graphs', type=Path,
                        help='Explicit byte-verified native multi-speaker model replacement')
    parser.add_argument('--hearing-frontend', type=Path,
                        help='Pinned native frontend paired with --hearing-graphs')
    parser.add_argument('--hearing-execution', choices=('cpu',),
                        help='Explicit new recognizer placement; does not change TTS or inherit GPU qualification')
    parser.add_argument('--voice-presets', type=Path, metavar='DIR',
                        help='Voice presets the replacement worker declares and the parent lacks; added to the model inventory')
    parser.add_argument('--uid-model', type=Path, help='Exact ECAPA graph; candidate only, no installed profile migration')
    parser.add_argument('--uid-policy', type=Path, help='Explicit model-bound calibration policy')
    parser.add_argument('--uid-policy-sha256', help='Exact policy bytes selected for this candidate')
    args = parser.parse_args()
    if any((args.uid_model, args.uid_policy, args.uid_policy_sha256)):
        if not all((args.uid_model, args.uid_policy, args.uid_policy_sha256, args.uid)):
            parser.error('UID model replacement requires model, policy, policy hash and native UID library')
        validate_uid_replacement(args.uid_model, args.uid_policy, args.uid_policy_sha256)
    if (args.hearing_graphs is None) != (args.hearing_frontend is None):
        parser.error('hearing graphs and frontend must be selected together')
    if args.hearing_execution and args.hearing_graphs is None:
        parser.error('hearing execution requires explicit hearing model replacement')
    if args.voice_presets is not None and (args.hearing_graphs is not None or args.uid_model is not None):
        parser.error('add voice presets in a rebuild of their own: one model change at a time')
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
                                          uid=args.uid, tts=args.tts, endpoint=args.endpoint))
    nemo = nemo_libraries(profile, args.nemo)
    replacements.update(nemo)
    ggml = ggml_libraries(profile, args.ggml)
    replacements.update(ggml)
    metal = metal_kernels(profile, replacements, args.metallib)
    for path in metal.values():
        bindings[str(path)] = sha(path)
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
    for name, source in metal.items():
        target = runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
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
    result = copy.deepcopy(frozen)
    if args.hearing_graphs is not None:
        result['hearing_execution'] = hearing_execution
        replace_hearing_models(result, runtime, args.hearing_graphs.resolve(),
                               args.hearing_frontend.resolve(), out, bindings)
    if args.uid_model is not None:
        added_notices.update(replace_uid_models(result, runtime, args.uid_model.resolve(), args.uid_policy.resolve(),
                           args.uid_policy_sha256, out, bindings))
    if args.voice_presets is not None:
        declared = [row for row in json.loads((out / 'settings.json').read_text()) if row.get('key') == 'tts_voice']
        if len(declared) != 1:
            raise ValueError('worker declares no voice setting')
        add_voice_presets(result, runtime, args.voice_presets.resolve(), declared[0].get('values'), out, bindings)
    else:
        # What a parent's rebuild added is not what this one adds.
        result.pop('voice_presets_added', None)
    updated = copy.deepcopy(profile)
    updated['files'] = runtime_inventory(runtime, target_platform=platform)
    updated['qualified'] = False
    delta = {n for n in set(profile['files']) | set(updated['files'])
             if profile['files'].get(n) != updated['files'].get(n)}
    allowed = set(replacements) | {'resources/settings.json'} | added_notices | set(metal)
    if args.hearing_graphs is not None:
        allowed.add('native-profile.json')
    if args.uid_model is not None:
        allowed.update(('native-profile.json', 'resources/uid-policy.json'))
    # The set options exist to change those libraries: a replacement whose
    # bytes equal the parent's names the wrong build and is never recorded.
    # A rebuilt ggml set may leave members its options do not reach unchanged.
    if not set(nemo) <= delta:
        raise ValueError('NeMo replacement leaves the parent bytes unchanged: '
                         + ', '.join(sorted(set(nemo) - delta)))
    if ggml and not set(ggml) & delta:
        raise ValueError('ggml replacement leaves the parent bytes unchanged')
    if not delta or not delta <= allowed:
        raise ValueError('unexpected runtime delta')
    (runtime / 'voice-runtime.json').write_text(json.dumps(updated, indent=2) + '\n')
    binding = sha(runtime / 'voice-runtime.json')
    bind_carrier(runtime, out / 'carrier-build.json', args.go.resolve())
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
                  changed_images=sorted(n for n in delta if n in replacements),
                  models_copied=result.get('models_copied', 0) if (args.hearing_graphs is not None or args.uid_model is not None
                                                               or args.voice_presets is not None) else 0,
                  execution_profile_changed='native-profile.json' in delta,
                  settings_changed='resources/settings.json' in delta,
                  settings_sha256=sha(out / 'settings.json'),
                  bindings=bindings)
    if nemo:
        result['nemo_replaced'] = sorted(nemo)
    if ggml:
        result['ggml_replaced'] = sorted(ggml)
    if metal:
        result['metal_kernels'] = {name: sha(runtime / name) for name in sorted(metal)}
    for name, source in replacements.items():
        if name != worker_name:
            result.setdefault('library_hashes', {})[Path(name).name] = sha(runtime / name)
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
