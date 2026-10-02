"""Stage a sealed Mac Nemotron checkpoint; no signing, installation or publication.

Preserve the verified parent's non-hearing models and settings. Bind the new
diarizer, official ONNX runtime and complete native dependency closure. Every
changed candidate requires fresh installed execution evidence.
"""
import argparse
import copy
import json
from pathlib import Path
import shutil
import subprocess

from scripts.native_checkpoint_binding import sha, verify_checkpoint
from scripts.package_native_runtime import bind_carrier, runtime_inventory
from scripts.rebuild_native_checkpoint import parent_bytes, relocate_macos, write_current_settings


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('parent', 'build', 'nemo', 'ort', 'model', 'out', 'go'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--parent-sha256', required=True)
    p.add_argument('--model-sha256', required=True)
    p.add_argument('--echo', type=Path, help='Prebuilt native echo stage, including licenses')
    a = p.parse_args()
    frozen, profile, bindings = parent_bytes(a.parent, a.parent_sha256)
    if profile['platform'] != 'darwin' or profile['arch'] != 'arm64':
        raise ValueError('Mac arm64 parent required')
    if sha(a.model) != a.model_sha256:
        raise ValueError('Nemotron model binding differs')
    a.out.mkdir(parents=True, exist_ok=False)
    runtime = a.out/'runtime'

    def install(source, destination):
        source = source.resolve(strict=True)
        bindings[str(source)] = sha(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha(destination) != bindings[str(source)]:
            raise ValueError('input changed during staging')

    for name in profile['files']:
        install(a.parent/'runtime'/name, runtime/name)
    install(a.build/'aii_voice_worker', runtime/'bin/aii_voice_worker')
    install(a.build/'libaii_voice_runtime.dylib', runtime/'lib/libaii_voice_runtime.dylib')
    install(a.ort, runtime/'lib/libonnxruntime.1.dylib')
    if a.echo:
        for name in ('libaii_native_echo.dylib', 'libwebrtc_aec3.dylib'):
            install(a.echo/'lib'/name, runtime/'lib'/name)
        for name in ('webrtc-aec3-LICENSE', 'native-echo-NOTICE'):
            install(a.echo/'licenses'/name, runtime/'resources/notices/native-echo'/name)
    # Copy dependency basenames as regular files, not install-time symlinks.
    # The loader's exact dependency spellings determine this closure.
    pending = ['libnemo_speech_asr_c.1.dylib']
    copied = set()
    while pending:
        name = pending.pop()
        if name in copied:
            continue
        if Path(name).name != name:
            raise ValueError('nonlocal native dependency')
        source = a.nemo/'lib'/name
        source.resolve(strict=True).relative_to(a.nemo.resolve())
        install(source, runtime/'lib'/name)
        copied.add(name)
        deps = subprocess.check_output(['/usr/bin/otool', '-L', str(source)], text=True)
        for line in deps.splitlines()[1:]:
            dep = line.strip().split(' (compatibility', 1)[0]
            if dep.startswith(('/usr/lib/', '/System/Library/')):
                continue
            if not dep.startswith('@rpath/'):
                raise ValueError('unexpected native dependency location')
            pending.append(Path(dep).name)
    for source in (a.nemo/'share/licenses/nemo-speech').iterdir():
        if source.is_file():
            install(source, runtime/'resources/notices/nemo-speech'/source.name)
    # All images are relocated after the complete closure exists.
    subprocess.run(['/usr/bin/install_name_tool', '-id', '@rpath/libonnxruntime.1.dylib',
                    str(runtime/'lib/libonnxruntime.1.dylib')], check=True)
    for image in sorted((runtime/'lib').glob('*.dylib')):
        deps = subprocess.check_output(['/usr/bin/otool', '-L', str(image)], text=True)
        for line in deps.splitlines()[1:]:
            dep = line.strip().split(' (compatibility', 1)[0]
            if Path(dep).name.startswith('libonnxruntime.') and image.name != 'libonnxruntime.1.dylib':
                subprocess.run(['/usr/bin/install_name_tool', '-change', dep,
                                '@loader_path/libonnxruntime.1.dylib', str(image)], check=True)
        relocate_macos(image, runtime)
    relocate_macos(runtime/'bin/aii_voice_worker', runtime)
    native = json.loads((runtime/'native-profile.json').read_text())
    if native['models']['asr'] != 'stt':
        raise ValueError('explicit native hearing layout required')
    native['asr_execution'] = dict(diarizer='nemotron', gpu=0, encoder_cuda=-1)
    (runtime/'native-profile.json').write_text(json.dumps(native, indent=2)+'\n')
    write_current_settings(runtime/'bin/aii_voice_worker', runtime, a.out)
    models = {}
    for name, row in frozen['models'].items():
        if name.startswith(('stt/diar_classifier/', 'stt/diar_preencode/')):
            continue
        install(Path(frozen['models_root'])/name, a.out/'data'/name)
        models[name] = row
    install(a.model, a.out/'data/stt/nemotron.gguf')
    models['stt/nemotron.gguf'] = dict(sha256=a.model_sha256, bytes=a.model.stat().st_size)
    updated = copy.deepcopy(profile)
    updated.update(files=runtime_inventory(runtime, target_platform='darwin'), qualified=False)
    (runtime/'voice-runtime.json').write_text(json.dumps(updated, indent=2)+'\n')
    bind_carrier(runtime, a.out/'carrier-build.json', a.go)
    record = json.loads((a.out/'carrier-build.json').read_text())
    result = dict(passed=True, signed=False, installed=False, published=False,
                  human_level_qualified=False, candidate_execution_validated=False,
                  hearing_replaced=True,
                  platform='macos', backend='native', scope=__doc__,
                  parent_freeze_sha256=a.parent_sha256,
                  runtime_manifest_sha256=sha(runtime/'voice-runtime.json'),
                  carrier_sha256=record['carrier_sha256'], worker_sha256=sha(runtime/'bin/aii_voice_worker'),
                  models_root=str((a.out/'data').resolve()), models=models,
                  model_bytes=sum(r['bytes'] for r in models.values()), bindings=bindings)
    bindings[str(Path(__file__).resolve())] = sha(Path(__file__))
    for path, digest in bindings.items():
        if sha(Path(path)) != digest:
            raise ValueError('bound input changed')
    (a.out/'freeze.json').write_text(json.dumps(result, indent=2)+'\n')
    verify_checkpoint(a.out)
    print(json.dumps({k: result[k] for k in ('runtime_manifest_sha256', 'carrier_sha256', 'model_bytes')}))


if __name__ == '__main__':
    main()
