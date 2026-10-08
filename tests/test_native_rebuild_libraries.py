from pathlib import Path
import hashlib
import json

import pytest

from scripts.rebuild_native_checkpoint import optional_libraries
from scripts.rebuild_native_checkpoint import replace_hearing_models
from scripts.rebuild_native_checkpoint import verified_parent_models
from scripts.rebuild_native_checkpoint import select_hearing_execution
from scripts.rebuild_native_checkpoint import replace_uid_models, validate_uid_replacement


def uid_fixture(tmp_path, monkeypatch):
    from scripts import rebuild_native_checkpoint as rebuild
    model=tmp_path/'encoder.onnx';model.write_bytes(b'synthetic graph contract fixture')
    binding='a'*64
    contract=tmp_path/'binding.json';contract.write_text('{}')
    exact={'model_bytes':model.stat().st_size,'model_sha256':rebuild.sha(model)}
    monkeypatch.setattr(rebuild,'ecapa_contract',lambda: (contract,exact,binding))
    policy=tmp_path/'policy.json'
    policy.write_text(json.dumps(dict(embedding_binding=binding,calibration_sha256='b'*64,
        threshold=.4,minimum_margin=.14,minimum_enrollment_samples=1)))
    return model,policy,rebuild.sha(policy)


def test_uid_replacement_preserves_other_models_and_clears_incompatible_prior_policy(tmp_path,monkeypatch):
    from scripts import rebuild_native_checkpoint as rebuild
    model,policy,digest=uid_fixture(tmp_path,monkeypatch)
    parent=tmp_path/'parent';runtime=tmp_path/'runtime';out=tmp_path/'out'
    for p in (parent/'uid',parent/'stt',runtime/'resources',out):p.mkdir(parents=True)
    (parent/'uid/model.onnx').write_bytes(b'old graph')
    (parent/'stt/model.onnx').write_bytes(b'unchanged hearing')
    original=dict(backend='metal',models={'uid':'uid/model.onnx','asr':'stt'},
        uid_policy='resources/uid-policy.json',uid_previous_policy='resources/old-policy.json',
        asr_execution={'diarizer':'nemotron','gpu':0,'encoder_cuda':-1})
    (runtime/'native-profile.json').write_text(json.dumps(original))
    (runtime/'resources/uid-policy.json').write_bytes(b'old policy')
    rows={name:dict(bytes=(parent/name).stat().st_size,sha256=rebuild.sha(parent/name))
          for name in ('uid/model.onnx','stt/model.onnx')}
    frozen=dict(models_root=str(parent),models=rows.copy());bindings={}
    notices=replace_uid_models(frozen,runtime,model,policy,digest,out,bindings)
    assert notices=={'resources/notices/ecapa-uid/'+n for n in
                     ('SpeechBrain-LICENSE','PocketFFT-LICENSE','PocketFFT-HEADER-LICENSE','MODEL-CARD.md','NOTICE')}
    for source in rebuild.uid_notice_sources().values():
        assert (runtime/'resources/notices/ecapa-uid'/source.name).read_bytes()==source.read_bytes()
        assert bindings[str(source)]==rebuild.sha(source)
    expected=dict(original);del expected['uid_previous_policy']
    assert json.loads((runtime/'native-profile.json').read_text())==expected
    assert (runtime/'resources/uid-policy.json').read_bytes()==policy.read_bytes()
    assert frozen['models']['stt/model.onnx']==rows['stt/model.onnx']
    assert (out/'data/stt/model.onnx').read_bytes()==b'unchanged hearing'
    assert (parent/'uid/model.onnx').read_bytes()==b'old graph'
    assert frozen['models']['uid/model.onnx']['sha256']==rebuild.sha(model)
    assert frozen['uid_replaced'] and not frozen['installed_profile_transition_validated']
    assert bindings[str(policy)]==digest


def test_uid_notices_are_bound_and_mandatory(monkeypatch):
    from scripts import rebuild_native_checkpoint as rebuild
    sources=rebuild.uid_notice_sources()
    original=rebuild.sha
    monkeypatch.setattr(rebuild,'sha',lambda path: '0'*64 if path==sources['PocketFFT-LICENSE'] else original(path))
    with pytest.raises(ValueError,match='notice binding differs'):rebuild.uid_notice_sources()


@pytest.mark.parametrize('field,value',[
    ('embedding_binding','c'*64),('threshold',True),('threshold',float('nan')),
    ('threshold',2),('minimum_margin',-1),('minimum_enrollment_samples',False),
    ('minimum_enrollment_samples',9),('calibration_sha256','not a digest'),('invented',1)])
def test_uid_policy_refuses_unbound_or_invalid_selection(tmp_path,monkeypatch,field,value):
    from scripts import rebuild_native_checkpoint as rebuild
    model,policy,_=uid_fixture(tmp_path,monkeypatch)
    doc=json.loads(policy.read_text());doc[field]=value;policy.write_text(json.dumps(doc))
    with pytest.raises(ValueError):validate_uid_replacement(model,policy,rebuild.sha(policy))


def test_uid_policy_hash_duplicates_and_symlinks_refused(tmp_path,monkeypatch):
    from scripts import rebuild_native_checkpoint as rebuild
    model,policy,digest=uid_fixture(tmp_path,monkeypatch)
    validate_uid_replacement(model,policy,digest)
    with pytest.raises(ValueError,match='byte binding'):validate_uid_replacement(model,policy,'0'*64)
    policy.write_text(policy.read_text()[:-1]+',"threshold":0.4}')
    with pytest.raises(ValueError,match='duplicate'):validate_uid_replacement(model,policy,rebuild.sha(policy))
    link=tmp_path/'link';link.symlink_to(model)
    with pytest.raises(ValueError,match='regular'):validate_uid_replacement(link,policy,rebuild.sha(policy))


def test_uid_model_bytes_must_match_native_contract(tmp_path,monkeypatch):
    model,policy,digest=uid_fixture(tmp_path,monkeypatch)
    model.write_bytes(b'x'*model.stat().st_size)
    with pytest.raises(ValueError,match='exact native ECAPA contract'):
        validate_uid_replacement(model,policy,digest)


def test_linux_packaging_resolves_even_unused_component_imports(tmp_path,monkeypatch):
    from scripts import rebuild_native_checkpoint as rebuild
    monkeypatch.setattr(rebuild.sys,'platform','linux')
    (tmp_path/'runtime/resources').mkdir(parents=True)
    seen=[]
    def describe(command,**kwargs):
        seen.append(kwargs)
        return b'[{"key":"fixture"}]'
    monkeypatch.setattr(rebuild.subprocess,'check_output',describe)
    rebuild.write_current_settings(tmp_path/'worker',tmp_path/'runtime',tmp_path)
    assert seen[0]['env']['LD_BIND_NOW']=='1' and seen[0]['timeout']==30


def test_old_recognizer_acceleration_requires_explicit_replacement(tmp_path):
    path=tmp_path/'native-profile.json'
    prior=dict(backend='vulkan',models={'tts':'tts','asr':'stt'},
               asr_execution=dict(provider='directml',adapter='high_performance'))
    path.write_text(json.dumps(prior));raw=path.read_bytes()
    with pytest.raises(ValueError,match='explicit hearing execution'):
        select_hearing_execution(tmp_path,None)
    assert path.read_bytes()==raw
    result=select_hearing_execution(tmp_path,'cpu')
    assert result==dict(provider='cpu',qualification_inherited=False)
    current=json.loads(path.read_text());del prior['asr_execution']
    assert current==prior  # TTS placement and every model path unchanged.
    raw=path.read_bytes()
    assert select_hearing_execution(tmp_path,None)==result
    assert path.read_bytes()==raw


def test_relocated_parent_models_require_identical_bytes(tmp_path):
    root=tmp_path/'relocated';root.mkdir()
    blob=b'unchanged pinned model';(root/'model').write_bytes(blob)
    expected=dict(bytes=len(blob),sha256=hashlib.sha256(blob).hexdigest())
    frozen=dict(models_root='/unavailable/prior-location',models={'model':expected.copy()})
    bindings={}
    verified_parent_models(frozen,bindings,root)
    assert frozen['models_root']==str(root.resolve())
    assert frozen['models']=={'model':expected}
    assert bindings=={str(root/'model'):expected['sha256']}
    (root/'model').write_bytes(b'x'*len(blob))
    with pytest.raises(ValueError,match='bytes differ'):
        verified_parent_models(frozen,{},root)
    (root/'model').unlink();(root/'other').write_bytes(blob);(root/'model').symlink_to(root/'other')
    with pytest.raises(ValueError,match='bytes differ'):
        verified_parent_models(frozen,{},root)


def test_relocated_parent_refuses_missing_and_parent_directory_escape(tmp_path):
    root=tmp_path/'root';root.mkdir()
    outside=tmp_path/'outside';outside.mkdir()
    raw=b'fixture';(outside/'model').write_bytes(raw)
    frozen=dict(models_root=str(root),models={'sub/model':dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())})
    with pytest.raises(ValueError,match='bytes differ'):verified_parent_models(frozen,{})
    (root/'sub').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='bytes differ'):verified_parent_models(frozen,{})


@pytest.mark.parametrize('platform,suffix,prefix', [
    ('darwin', '.dylib', 'lib'), ('linux', '.so', 'lib'), ('windows', '.dll', '')])
def test_replacements_name_existing_components_only(tmp_path, platform, suffix, prefix):
    stems = ('aiii_uid_frontend', 'aii_native_uid', 'native_pocket_resident', 'aii_native_endpoint')
    names = ['lib/'+prefix+stem+suffix for stem in stems]
    paths = [tmp_path/(stem+suffix) for stem in stems]
    for path in paths:
        path.write_bytes(b'explicitly bound image fixture')
    profile = dict(platform=platform, files=dict.fromkeys(names+['lib/vendor'+suffix]))
    assert optional_libraries(profile) == {}
    assert optional_libraries(profile, uid_frontend=paths[0], uid=paths[1], tts=paths[2], endpoint=paths[3]) == dict(zip(names, paths))
    assert optional_libraries(profile, uid_frontend=paths[0]) == {names[0]: paths[0]}
    del profile['files'][names[0]]
    with pytest.raises(ValueError, match='layout differs'):
        optional_libraries(profile, uid_frontend=paths[0])


def test_ambiguous_target_and_missing_source_refused(tmp_path):
    source = tmp_path/'fixture'
    profile = dict(platform='linux', files={'lib/aiii_uid_frontend.so': {}, 'lib/libaiii_uid_frontend.so': {}})
    with pytest.raises(ValueError, match='regular library'):
        optional_libraries(profile, uid_frontend=source)
    source.write_bytes(b'fixture')
    with pytest.raises(ValueError, match='layout differs'):
        optional_libraries(profile, uid_frontend=source)


def test_symlink_and_unsafe_destination_refused(tmp_path):
    source = tmp_path/'fixture'
    source.write_bytes(b'fixture')
    link = tmp_path/'link'
    link.symlink_to(source)
    profile = dict(platform='linux', files={'lib/libaiii_uid_frontend.so': {}})
    with pytest.raises(ValueError, match='regular library'):
        optional_libraries(profile, uid_frontend=link)
    profile['files'] = {'../libaiii_uid_frontend.so': {}}
    with pytest.raises(ValueError):
        optional_libraries(profile, uid_frontend=source)


def test_explicit_hearing_replacement_preserves_other_models(tmp_path, monkeypatch):
    from scripts import prove_native_multitalker
    parent, runtime, graphs, frontend, out = [tmp_path/name for name in
                                            ('parent', 'runtime', 'graphs', 'frontend', 'out')]
    for path in (parent/'stt', parent/'uid', runtime, graphs/'asr_encoder', frontend, out):
        path.mkdir(parents=True)
    (parent/'stt/old.bin').write_bytes(b'old recognizer')
    (parent/'uid/model.onnx').write_bytes(b'unchanged speaker model')
    (graphs/'asr_encoder/model.onnx').write_bytes(b'explicit selected graph fixture')
    (graphs/'result.json').write_text('{}')
    mel = b'\0'*(128*257*4)
    (frontend/'mel.f32').write_bytes(mel)
    (frontend/'result.json').write_text(json.dumps({'files': {'mel.f32': hashlib.sha256(mel).hexdigest()}}))
    (runtime/'native-profile.json').write_text(json.dumps({'models': {'asr':'stt', 'asr_mel':'stt/mel.f32'}}))
    record = dict(compaction={'graphs': {'asr_encoder': {}}}, artifacts={'asr_encoder/model.onnx': {}})
    monkeypatch.setattr(prove_native_multitalker, 'verify_graphs', lambda path: record)
    frozen = dict(models_root=str(parent), models={'stt/old.bin':{}, 'uid/model.onnx':{}})
    bindings = {}
    replace_hearing_models(frozen, runtime, graphs, frontend, out, bindings)
    assert set(frozen['models']) == {'stt/asr_encoder/model.onnx', 'stt/mel.f32', 'uid/model.onnx'}
    assert (out/'data/uid/model.onnx').read_bytes() == (parent/'uid/model.onnx').read_bytes()
    assert not (out/'data/stt/old.bin').exists()
    assert (parent/'stt/old.bin').read_bytes() == b'old recognizer'
    assert str(graphs/'result.json') in bindings and frozen['hearing_replaced']
    (frontend/'mel.f32').write_bytes(b'changed')
    with pytest.raises(ValueError, match='frontend binding'):
        replace_hearing_models(frozen, runtime, graphs, frontend, tmp_path/'bad', {})


# The NeMo-Speech.cpp diarizer/ASR library: its implementation and C API,
# replaced only as a whole set, by the names each parent runtime declares.
NEMO = {'darwin': ('lib/libnemo_speech_asr.dylib', 'lib/libnemo_speech_asr_c.1.dylib'),
        'linux': ('lib/libnemo_speech_asr.so', 'lib/libnemo_speech_asr_c.so.1'),
        'windows': ('bin/nemo_speech_asr.dll', 'bin/nemo_speech_asr_c.dll')}


def nemo_sources(tmp_path, names):
    built = tmp_path / 'nemo-build'
    built.mkdir(exist_ok=True)
    paths = []
    for name in names:
        path = built / Path(name).name
        path.write_bytes(b'rebuilt with the stream-copy patch: ' + name.encode())
        paths.append(path)
    return paths


@pytest.mark.parametrize('platform', sorted(NEMO))
def test_nemo_library_set_is_replaced_whole_by_its_declared_names(tmp_path, platform):
    from scripts import rebuild_native_checkpoint as rebuild
    names = NEMO[platform]
    profile = dict(platform=platform, files=dict.fromkeys(
        [*names, 'lib/libggml.0.dylib', 'resources/notices/nemo-speech/LICENSE']))
    sources = nemo_sources(tmp_path, names)
    assert rebuild.nemo_libraries(profile, None) == {}
    assert rebuild.nemo_libraries(profile, []) == {}
    assert rebuild.nemo_libraries(profile, sources) == dict(zip(names, (p.resolve() for p in sources)))
    assert rebuild.nemo_libraries(profile, sources[::-1]) == dict(zip(names, (p.resolve() for p in sources)))


@pytest.mark.parametrize('fault,match', [
    ('partial', 'whole'), ('unknown', 'unknown NeMo'), ('other-platform', 'unknown NeMo'),
    ('unversioned-link-name', 'unknown NeMo'), ('duplicate', 'twice'), ('symlink', 'regular'),
    ('missing', 'regular'), ('undeclared', 'does not declare'), ('extra-declared', 'does not declare'),
    ('elsewhere', 'does not declare')])
def test_nemo_replacement_refuses_partial_unknown_or_undeclared_sets(tmp_path, fault, match):
    from scripts import rebuild_native_checkpoint as rebuild
    names = NEMO['darwin']
    profile = dict(platform='darwin', files=dict.fromkeys([*names, 'lib/libggml.0.dylib']))
    sources = nemo_sources(tmp_path, names)
    if fault == 'partial': sources = sources[:1]
    if fault == 'unknown': sources.append(nemo_sources(tmp_path, ['lib/libggml.0.dylib'])[0])
    if fault == 'other-platform': sources[1] = nemo_sources(tmp_path, ['lib/libnemo_speech_asr_c.so.1'])[0]
    if fault == 'unversioned-link-name': sources[1] = nemo_sources(tmp_path, ['lib/libnemo_speech_asr_c.dylib'])[0]
    if fault == 'duplicate': sources.append(sources[0])
    if fault == 'symlink':
        link = tmp_path / 'linked'; link.mkdir(); (link / sources[0].name).symlink_to(sources[0]); sources[0] = link / sources[0].name
    if fault == 'missing': sources[0].unlink()
    if fault == 'undeclared': del profile['files'][names[1]]
    if fault == 'extra-declared': profile['files']['lib/libnemo_speech_asr_c.dylib'] = None
    if fault == 'elsewhere':
        del profile['files'][names[0]]; profile['files']['bin/libnemo_speech_asr.dylib'] = None
    with pytest.raises(ValueError, match=match):
        rebuild.nemo_libraries(profile, sources)


def sealed_windows_parent(tmp_path):
    """A small sealed Windows parent: no relocation, real inventory checks."""
    from scripts import rebuild_native_checkpoint as rebuild
    from scripts.package_native_runtime import runtime_inventory
    parent = tmp_path / 'parent'
    runtime = parent / 'runtime'
    images = {'bin/aii_voice_worker.exe': b'worker', 'bin/aii_native_asr.dll': b'asr',
              'bin/aii_voice_runtime.dll': b'session', 'bin/ggml.dll': b'parent ggml',
              'bin/nemo_speech_asr.dll': b'parent nemo', 'bin/nemo_speech_asr_c.dll': b'parent nemo c api',
              'resources/settings.json': b'[{"key":"fixture"}]\n',
              'resources/notices/nemo-speech/LICENSE': b'license'}
    for name, raw in images.items():
        (runtime / name).parent.mkdir(parents=True, exist_ok=True)
        (runtime / name).write_bytes(raw)
    (runtime / 'aii-voice-t3.exe').write_bytes(b'parent carrier')
    profile = dict(schema='aiii.voice.native-runtime', platform='windows', arch='amd64', qualified=False,
                   files=runtime_inventory(runtime, target_platform='windows'))
    (runtime / 'voice-runtime.json').write_text(json.dumps(profile))
    models = tmp_path / 'models'; models.mkdir()
    (models / 'model.bin').write_bytes(b'unchanged model')
    manifest, carrier = rebuild.sha(runtime / 'voice-runtime.json'), rebuild.sha(runtime / 'aii-voice-t3.exe')
    (parent / 'carrier-build.json').write_text(json.dumps(dict(carrier_sha256=carrier, runtime_manifest_sha256=manifest)))
    (parent / 'freeze.json').write_text(json.dumps(dict(
        passed=True, signed=False, installed=False, runtime_manifest_sha256=manifest, carrier_sha256=carrier,
        models_root=str(models), models={'model.bin': dict(bytes=15, sha256=rebuild.sha(models / 'model.bin'))},
        library_hashes={'nemo_speech_asr.dll': rebuild.sha(runtime / 'bin/nemo_speech_asr.dll')},
        authenticode_derivation=dict(signed_components=['bin/nemo_speech_asr.dll']),
        runtime_authenticode_verified=True, candidate_execution_validated=True)))
    return parent, images


def run_rebuild(tmp_path, monkeypatch, parent, extra):
    from scripts import rebuild_native_checkpoint as rebuild
    def settings(worker, runtime, out):
        raw = (runtime / 'resources/settings.json').read_bytes()
        (out / 'settings.json').write_bytes(raw)
        return json.loads(raw)
    def carrier(runtime, record, go):
        (runtime / 'aii-voice-t3.exe').write_bytes(b'rebuilt carrier')
        record.write_text(json.dumps(dict(carrier_sha256=rebuild.sha(runtime / 'aii-voice-t3.exe'),
                                          runtime_manifest_sha256=rebuild.sha(runtime / 'voice-runtime.json'))))
    monkeypatch.setattr(rebuild, 'write_current_settings', settings)
    monkeypatch.setattr(rebuild, 'bind_carrier', carrier)
    monkeypatch.setattr(rebuild, 'verify_checkpoint', lambda out: None)
    runtime = parent / 'runtime'
    out = tmp_path / 'candidate'
    monkeypatch.setattr(rebuild.sys, 'argv', ['rebuild_native_checkpoint',
        '--parent', str(parent), '--parent-sha256', rebuild.sha(parent / 'freeze.json'),
        '--worker', str(runtime / 'bin/aii_voice_worker.exe'), '--asr', str(runtime / 'bin/aii_native_asr.dll'),
        '--session-library', str(runtime / 'bin/aii_voice_runtime.dll'),
        '--out', str(out), '--go', str(tmp_path / 'go'), *extra])
    rebuild.main()
    return out, json.loads((out / 'freeze.json').read_text())


def test_rebuild_replaces_the_nemo_library_and_records_every_image_as_changed(tmp_path, monkeypatch):
    from scripts import rebuild_native_checkpoint as rebuild
    parent, images = sealed_windows_parent(tmp_path)
    before = {p: p.read_bytes() for p in parent.rglob('*') if p.is_file()}
    sources = nemo_sources(tmp_path, NEMO['windows'])
    out, frozen = run_rebuild(tmp_path, monkeypatch, parent, [a for s in sources for a in ('--nemo', str(s))])
    runtime = out / 'runtime'
    for name, source in zip(NEMO['windows'], sources):
        assert (runtime / name).read_bytes() == source.read_bytes()
        assert frozen['libraries'][Path(name).name] == dict(
            source=str(source.resolve()), source_sha256=rebuild.sha(source), relocated_sha256=rebuild.sha(source))
        assert frozen['library_hashes'][Path(name).name] == rebuild.sha(source)
        assert frozen['bindings'][str(source.resolve())] == rebuild.sha(source)
    # Unchanged images keep the parent's verified bytes; only NeMo changed.
    for name, raw in images.items():
        if name not in NEMO['windows']:
            assert (runtime / name).read_bytes() == raw
    assert frozen['changed_images'] == sorted(NEMO['windows'])
    assert frozen['nemo_replaced'] == sorted(NEMO['windows'])
    assert not frozen['candidate_execution_validated'] and not frozen['runtime_authenticode_verified']
    assert 'authenticode_derivation' not in frozen
    profile = json.loads((runtime / 'voice-runtime.json').read_text())
    assert profile['qualified'] is False
    assert {n: profile['files'][n]['sha256'] for n in NEMO['windows']} == {
        n: rebuild.sha(s) for n, s in zip(NEMO['windows'], sources)}
    assert {p: p.read_bytes() for p in parent.rglob('*') if p.is_file()} == before


def test_rebuild_refuses_a_nemo_replacement_that_changes_nothing(tmp_path, monkeypatch):
    parent, _ = sealed_windows_parent(tmp_path)
    same = [parent / 'runtime' / name for name in NEMO['windows']]
    with pytest.raises(ValueError, match='leaves the parent'):
        run_rebuild(tmp_path, monkeypatch, parent, [a for s in same for a in ('--nemo', str(s))])
    assert not (tmp_path / 'candidate/freeze.json').exists()


def test_rebuild_refuses_a_partial_nemo_set_before_writing(tmp_path, monkeypatch):
    parent, _ = sealed_windows_parent(tmp_path)
    sources = nemo_sources(tmp_path, NEMO['windows'])
    with pytest.raises(ValueError, match='whole'):
        run_rebuild(tmp_path, monkeypatch, parent, ['--nemo', str(sources[0])])
    assert not (tmp_path / 'candidate').exists()



GGML = ('lib/libggml.0.dylib', 'lib/libggml-base.0.dylib', 'lib/libggml-blas.0.dylib',
        'lib/libggml-cpu.0.dylib', 'lib/libggml-metal.0.dylib')
POCKET, POCKET_KERNELS = 'lib/libnative_pocket_resident.dylib', 'lib/libnative_pocket_resident.metallib'
LOADER = b'the image that carries ggml-metal could not be located'


def image(path, *, embedded=False, loader=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'\xcf\xfa\xed\xfe' + (b'kernel void kernel_add(' if embedded else b'no shader source')
                     + (LOADER if loader else b'') + b'\0' * 64)
    return path


def kernels(path, magic=b'MTLB'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(magic + b'\0' * 64)
    return path


def test_ggml_library_set_is_replaced_whole_on_macos_only(tmp_path):
    from scripts import rebuild_native_checkpoint as rebuild
    profile = dict(platform='darwin', files=dict.fromkeys([*GGML, 'lib/libnemo_speech_asr.dylib']))
    sources = [image(tmp_path / Path(n).name) for n in GGML]
    assert rebuild.ggml_libraries(profile, None) == {}
    assert rebuild.ggml_libraries(profile, sources) == dict(zip(GGML, (p.resolve() for p in sources)))
    with pytest.raises(ValueError, match='whole'):
        rebuild.ggml_libraries(profile, sources[:-1])
    with pytest.raises(ValueError, match='darwin only'):
        rebuild.ggml_libraries(dict(platform='linux', files={}), sources)


def test_each_image_without_shader_source_gets_the_kernels_its_loader_reads(tmp_path):
    from scripts.rebuild_native_checkpoint import metal_kernels
    profile = dict(platform='darwin', files=dict.fromkeys([*GGML, POCKET, 'bin/aii_voice_worker']))
    replaced = {'lib/libggml-metal.0.dylib': image(tmp_path / 'libggml-metal.0.dylib'),
                POCKET: image(tmp_path / 'libnative_pocket_resident.dylib', loader=True)}
    given = [kernels(tmp_path / 'k' / 'default.metallib'), kernels(tmp_path / 'k' / 'libnative_pocket_resident.metallib')]
    assert metal_kernels(profile, replaced, given) == {
        'bin/default.metallib': given[0].resolve(), POCKET_KERNELS: given[1].resolve()}
    # Images that still embed their source need nothing, and nothing replaced needs nothing.
    embedded = {POCKET: image(tmp_path / 'e' / 'libnative_pocket_resident.dylib', embedded=True)}
    assert metal_kernels(profile, embedded, None) == {}
    assert metal_kernels(profile, {}, None) == {}


@pytest.mark.parametrize('fault,match', [
    ('no-kernels', 'is required'), ('unreplaced-image', 'only together'), ('embedded-image', 'never reads'),
    ('embedded-beside-declared', 'never reads'), ('unpatched-loader', 'metal-library-beside'),
    ('unknown-name', 'no Metal image reads'), ('not-a-metallib', 'not a compiled Metal'),
    ('twice', 'twice'), ('symlink', 'regular')])
def test_metal_kernels_refuse_what_would_not_load(tmp_path, fault, match):
    from scripts.rebuild_native_checkpoint import metal_kernels
    profile = dict(platform='darwin', files=dict.fromkeys([*GGML, POCKET]))
    replaced = {POCKET: image(tmp_path / 'libnative_pocket_resident.dylib', loader=fault != 'unpatched-loader',
                              embedded=fault in ('embedded-image', 'embedded-beside-declared'))}
    given = [kernels(tmp_path / 'k' / 'libnative_pocket_resident.metallib',
                     magic=b'\x7fELF' if fault == 'not-a-metallib' else b'MTLB')]
    if fault == 'no-kernels': given = []
    if fault == 'unreplaced-image': given.append(kernels(tmp_path / 'k' / 'default.metallib'))
    if fault == 'embedded-beside-declared': given = []; profile['files'][POCKET_KERNELS] = None
    if fault == 'unknown-name': given = [kernels(tmp_path / 'k' / 'ggml.metallib')]
    if fault == 'twice': given.append(given[0])
    if fault == 'symlink':
        link = tmp_path / 'l'; link.mkdir(); (link / given[0].name).symlink_to(given[0]); given = [link / given[0].name]
    with pytest.raises(ValueError, match=match):
        metal_kernels(profile, replaced, given)


def darwin_rebuild(tmp_path, monkeypatch, change_ggml=True):
    from scripts import rebuild_native_checkpoint as rebuild
    from scripts.package_native_runtime import runtime_inventory
    parent = tmp_path / 'parent'
    runtime = parent / 'runtime'
    for name in ('bin/aii_voice_worker', 'lib/libaii_native_asr.dylib', 'lib/libaii_voice_runtime.dylib', *GGML):
        image(runtime / name)
    image(runtime / POCKET, embedded=True)
    (runtime / 'resources').mkdir()
    (runtime / 'resources/settings.json').write_text('[{"key":"fixture"}]\n')
    (runtime / 'aii-voice-t3').write_bytes(b'parent carrier')
    profile = dict(schema='aiii.voice.native-runtime', platform='darwin', arch='arm64', qualified=False,
                   files=runtime_inventory(runtime, target_platform='darwin'))
    (runtime / 'voice-runtime.json').write_text(json.dumps(profile))
    manifest, carrier = rebuild.sha(runtime / 'voice-runtime.json'), rebuild.sha(runtime / 'aii-voice-t3')
    (parent / 'carrier-build.json').write_text(json.dumps(dict(carrier_sha256=carrier, runtime_manifest_sha256=manifest)))
    (parent / 'freeze.json').write_text(json.dumps(dict(passed=True, signed=False, installed=False,
        runtime_manifest_sha256=manifest, carrier_sha256=carrier, models_root=str(tmp_path), models={})))
    def settings(worker, runtime, out):
        (out / 'settings.json').write_bytes((runtime / 'resources/settings.json').read_bytes())
    def bind(runtime, record, go):
        (runtime / 'aii-voice-t3').write_bytes(b'rebuilt carrier')
        record.write_text(json.dumps(dict(carrier_sha256=rebuild.sha(runtime / 'aii-voice-t3'),
                                          runtime_manifest_sha256=rebuild.sha(runtime / 'voice-runtime.json'))))
    monkeypatch.setattr(rebuild, 'write_current_settings', settings)
    monkeypatch.setattr(rebuild, 'bind_carrier', bind)
    monkeypatch.setattr(rebuild, 'verify_checkpoint', lambda out: None)
    monkeypatch.setattr(rebuild, 'relocate_macos', lambda path, runtime: None)
    new = tmp_path / 'new'
    ggml = [image(new / 'ggml' / Path(n).name) for n in GGML]
    if change_ggml:  # only the CPU and Metal members change
        ggml[3].write_bytes(ggml[3].read_bytes() + b'baseline cpu')
        ggml[4].write_bytes(ggml[4].read_bytes() + b'no embedded source')
    pocket = image(new / 'libnative_pocket_resident.dylib', loader=True)
    shaders = [kernels(new / 'k' / 'default.metallib'), kernels(new / 'k' / 'libnative_pocket_resident.metallib', b'MTLB2')]
    out = tmp_path / 'candidate'
    monkeypatch.setattr(rebuild.sys, 'argv', ['rebuild_native_checkpoint',
        '--parent', str(parent), '--parent-sha256', rebuild.sha(parent / 'freeze.json'),
        '--worker', str(runtime / 'bin/aii_voice_worker'), '--asr', str(runtime / 'lib/libaii_native_asr.dylib'),
        '--session-library', str(runtime / 'lib/libaii_voice_runtime.dylib'), '--out', str(out), '--go', str(tmp_path),
        '--tts', str(pocket), *[a for g in ggml for a in ('--ggml', str(g))],
        *[a for k in shaders for a in ('--metallib', str(k))]])
    rebuild.main()
    return out, json.loads((out / 'freeze.json').read_text()), shaders


def test_rebuild_installs_the_kernels_beside_the_image_that_reads_them(tmp_path, monkeypatch):
    from scripts import rebuild_native_checkpoint as rebuild
    out, frozen, shaders = darwin_rebuild(tmp_path, monkeypatch)
    built = out / 'runtime'
    assert (built / 'bin/default.metallib').read_bytes() == shaders[0].read_bytes()
    assert (built / POCKET_KERNELS).read_bytes() == shaders[1].read_bytes()
    assert frozen['metal_kernels'] == {'bin/default.metallib': rebuild.sha(shaders[0]), POCKET_KERNELS: rebuild.sha(shaders[1])}
    assert frozen['ggml_replaced'] == sorted(GGML)
    assert set(frozen['changed_images']) == {'lib/libggml-cpu.0.dylib', 'lib/libggml-metal.0.dylib', POCKET}
    declared = json.loads((built / 'voice-runtime.json').read_text())['files']
    assert {'bin/default.metallib', POCKET_KERNELS} <= set(declared)
    assert all(frozen['bindings'][str(k.resolve())] == rebuild.sha(k) for k in shaders)


def test_rebuild_refuses_a_ggml_set_that_changes_nothing(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='ggml replacement leaves the parent bytes unchanged'):
        darwin_rebuild(tmp_path, monkeypatch, change_ggml=False)
    assert not (tmp_path / 'candidate/freeze.json').exists()


# Voice presets a replacement worker newly declares: added to the inventory,
# never substituted, and afterwards exactly the voices the worker declares.
def voice_fixture(tmp_path):
    from scripts import rebuild_native_checkpoint as rebuild
    parent, runtime, presets, out = [tmp_path / name for name in ('models', 'runtime', 'presets', 'out')]
    for path in (parent / 'tts/embeddings', parent / 'uid', runtime, presets, out):
        path.mkdir(parents=True)
    held = {'tts/model.safetensors': b'speech model', 'tts/embeddings/first.safetensors': b'first preset',
            'uid/model.onnx': b'speaker model'}
    for name, raw in held.items():
        (parent / name).write_bytes(raw)
    (presets / 'second.safetensors').write_bytes(b'second preset')
    (presets / 'third.safetensors').write_bytes(b'third preset, longer')
    (runtime / 'native-profile.json').write_text(json.dumps({'models': {'tts': 'tts'}}))
    frozen = dict(models_root=str(parent), model_bytes=sum(map(len, held.values())),
                  models={name: dict(sha256=rebuild.sha(parent / name), bytes=len(raw)) for name, raw in held.items()})
    return frozen, runtime, presets, out, parent, held


def test_declared_voice_presets_are_added_and_every_parent_model_is_carried(tmp_path):
    from scripts.rebuild_native_checkpoint import add_voice_presets, sha
    frozen, runtime, presets, out, parent, held = voice_fixture(tmp_path)
    before = {p: p.read_bytes() for p in parent.rglob('*') if p.is_file()}
    bindings = {}
    add_voice_presets(frozen, runtime, presets, ['first', 'second', 'third'], out, bindings)
    assert set(frozen['models']) == set(held) | {'tts/embeddings/second.safetensors', 'tts/embeddings/third.safetensors'}
    assert frozen['models_root'] == str(out / 'data') and frozen['models_copied'] == 5
    assert frozen['voice_presets_added'] == ['second', 'third']
    for name, row in frozen['models'].items():
        assert sha(out / 'data' / name) == row['sha256'] and (out / 'data' / name).stat().st_size == row['bytes']
    for name, raw in held.items():
        assert (out / 'data' / name).read_bytes() == raw
    assert frozen['model_bytes'] == sum(row['bytes'] for row in frozen['models'].values())
    assert bindings[str(presets / 'second.safetensors')] == sha(presets / 'second.safetensors')
    assert {p: p.read_bytes() for p in parent.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('fault,match', [
    ('undeclared', 'undeclared third'),
    ('missing', 'missing fourth'),
    ('repeated', 'differ from the declared voices'),
    ('held', 'already in the parent inventory: first'),
    ('other-file', 'regular nonempty .safetensors file: notes.txt'),
    ('empty', 'regular nonempty .safetensors file: third.safetensors'),
    ('link', 'regular nonempty .safetensors file: linked.safetensors'),
    ('nothing', 'no voice preset to add'),
    ('directory-link', 'regular voice preset directory required'),
    ('parent-changed', 'parent model bytes differ: uid/model.onnx'),
    ('bound', 'exceeds accelerator profile bound'),
    ('no-bank', 'names no speech model directory'),
])
def test_voice_presets_that_do_not_match_the_declaration_are_refused_before_a_checkpoint_exists(tmp_path, fault, match):
    from scripts.rebuild_native_checkpoint import add_voice_presets
    frozen, runtime, presets, out, parent, held = voice_fixture(tmp_path)
    declared = ['first', 'second', 'third']
    if fault == 'undeclared':
        declared = ['first', 'second']
    elif fault == 'missing':
        declared = ['first', 'second', 'third', 'fourth']
    elif fault == 'repeated':
        declared = ['first', 'second', 'third', 'third']
    elif fault == 'held':
        (presets / 'first.safetensors').write_bytes(b'another first')
    elif fault == 'other-file':
        (presets / 'notes.txt').write_text('not a preset')
    elif fault == 'empty':
        (presets / 'third.safetensors').write_bytes(b'')
    elif fault == 'link':
        (presets / 'linked.safetensors').symlink_to(presets / 'second.safetensors')
    elif fault == 'nothing':
        for path in presets.iterdir():
            path.unlink()
    elif fault == 'directory-link':
        (tmp_path / 'elsewhere').symlink_to(presets, target_is_directory=True)
        presets = tmp_path / 'elsewhere'
    elif fault == 'parent-changed':
        (parent / 'uid/model.onnx').write_bytes(b'another model')
    elif fault == 'bound':
        for index in range(126):
            frozen['models'][f'uid/extra-{index}.bin'] = dict(sha256='0' * 64, bytes=1)
    elif fault == 'no-bank':
        (runtime / 'native-profile.json').write_text(json.dumps({'models': {}}))
    with pytest.raises(ValueError, match=match):
        add_voice_presets(frozen, runtime, presets, declared, out, {})
    # Refusals that need no file read happen before anything is written.
    if fault not in ('parent-changed',):
        assert not (out / 'data').exists()


def test_rebuild_adds_the_presets_its_worker_declares(tmp_path, monkeypatch):
    from scripts import rebuild_native_checkpoint as rebuild
    parent, images = sealed_windows_parent(tmp_path)
    runtime = parent / 'runtime'
    # A parent whose speech model and one preset are in its inventory, and a worker that now declares two voices.
    models = tmp_path / 'models'
    (models / 'tts/embeddings').mkdir(parents=True)
    (models / 'tts/embeddings/first.safetensors').write_bytes(b'first preset')
    (runtime / 'native-profile.json').write_text(json.dumps({'models': {'tts': 'tts'}}))
    (runtime / 'resources/settings.json').write_text(json.dumps([{'key': 'tts_voice', 'values': ['first', 'second']}]) + '\n')
    (runtime / 'bin/aii_voice_worker.exe').write_bytes(b'worker that declares two voices')
    from scripts.package_native_runtime import runtime_inventory
    profile = json.loads((runtime / 'voice-runtime.json').read_text())
    profile['files'] = runtime_inventory(runtime, target_platform='windows')
    (runtime / 'voice-runtime.json').write_text(json.dumps(profile))
    frozen = json.loads((parent / 'freeze.json').read_text())
    manifest = rebuild.sha(runtime / 'voice-runtime.json')
    frozen.update(runtime_manifest_sha256=manifest)
    frozen['models']['tts/embeddings/first.safetensors'] = dict(bytes=12, sha256=rebuild.sha(models / 'tts/embeddings/first.safetensors'))
    (parent / 'freeze.json').write_text(json.dumps(frozen))
    record = json.loads((parent / 'carrier-build.json').read_text()); record['runtime_manifest_sha256'] = manifest
    (parent / 'carrier-build.json').write_text(json.dumps(record))
    presets = tmp_path / 'presets'; presets.mkdir()
    (presets / 'second.safetensors').write_bytes(b'second preset')
    worker = tmp_path / 'new-worker.exe'; worker.write_bytes(b'rebuilt worker that declares two voices')
    out, result = run_rebuild(tmp_path, monkeypatch, parent, ['--worker', str(worker), '--voice-presets', str(presets)])
    assert result['voice_presets_added'] == ['second'] and result['models_copied'] == 3
    assert set(result['models']) == {'model.bin', 'tts/embeddings/first.safetensors', 'tts/embeddings/second.safetensors'}
    assert result['models_root'] == str(out / 'data')
    assert (out / 'data/tts/embeddings/second.safetensors').read_bytes() == b'second preset'
    assert result['changed_images'] == ['bin/aii_voice_worker.exe']


def test_a_rebuild_that_adds_no_preset_does_not_repeat_what_its_parent_added(tmp_path, monkeypatch):
    parent, images = sealed_windows_parent(tmp_path)
    frozen = json.loads((parent / 'freeze.json').read_text())
    frozen['voice_presets_added'] = ['second']
    (parent / 'freeze.json').write_text(json.dumps(frozen))
    sources = nemo_sources(tmp_path, NEMO['windows'])
    out, result = run_rebuild(tmp_path, monkeypatch, parent, [a for s in sources for a in ('--nemo', str(s))])
    assert 'voice_presets_added' not in result and result['models_copied'] == 0
