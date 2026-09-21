from pathlib import Path
import hashlib
import json

import pytest

from scripts.rebuild_native_checkpoint import optional_libraries
from scripts.rebuild_native_checkpoint import replace_hearing_models
from scripts.rebuild_native_checkpoint import verified_parent_models
from scripts.rebuild_native_checkpoint import select_hearing_execution


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
    stems = ('aiii_uid_frontend', 'aii_native_uid', 'native_pocket_resident')
    names = ['lib/'+prefix+stem+suffix for stem in stems]
    paths = [tmp_path/(stem+suffix) for stem in stems]
    for path in paths:
        path.write_bytes(b'explicitly bound image fixture')
    profile = dict(platform=platform, files=dict.fromkeys(names+['lib/vendor'+suffix]))
    assert optional_libraries(profile) == {}
    assert optional_libraries(profile, uid_frontend=paths[0], uid=paths[1], tts=paths[2]) == dict(zip(names, paths))
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
