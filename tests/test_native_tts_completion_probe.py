from pathlib import Path
import json,subprocess,sys
import shutil
import pytest
from scripts.probe_native_tts_completion import resident_library
from scripts.native_tts_completion_contract import BEFORE,AFTER,restore_frame_limit_fault
from scripts.audit_native_tts_completion import binary_completion


@pytest.mark.parametrize('platform,name',[
    ('win32','bin/native_pocket_resident.dll'),
    ('linux','lib/libnative_pocket_resident.so'),
    ('darwin','lib/libnative_pocket_resident.dylib'),
    ('darwin','lib/libaii_voice_runtime.dylib'),
])
def test_declared_resident_layout(platform,name):
    assert resident_library(Path('/runtime'),{'files':{name:{}}},platform)==Path('/runtime')/name


def test_undeclared_dll_is_not_a_binding():
    with pytest.raises(AssertionError,match='no declared'):
        resident_library(Path('/runtime'),{'files':{'lib/native_pocket_resident.dll':{}}},'win32')


def test_dedicated_mac_resident_is_the_one_linked_by_owner():
    m={'files':{'lib/libnative_pocket_resident.dylib':{},'lib/libaii_voice_runtime.dylib':{}}}
    assert resident_library(Path('/runtime'),m,'darwin').name=='libnative_pocket_resident.dylib'


def test_source_boundary_preserves_every_other_byte_and_is_idempotent():
    original='prefix\n'+BEFORE+'\n    }\nrest\n'
    fixed=restore_frame_limit_fault(original)
    assert fixed=='prefix\n'+AFTER+'\n    }\nrest\n'
    assert restore_frame_limit_fault(fixed)==fixed


@pytest.mark.parametrize('text',['',BEFORE+BEFORE,AFTER+AFTER,BEFORE+AFTER])
def test_unknown_or_ambiguous_source_refuses(text):
    with pytest.raises(ValueError,match='completion boundary changed'):
        restore_frame_limit_fault(text)


def test_real_history_stage_retains_the_fault(tmp_path):
    root=Path(__file__).resolve().parents[1];out=tmp_path/'history'
    subprocess.run([sys.executable,'-m','scripts.stage_pocket_capacity_history',str(out)],cwd=root,check=True,capture_output=True)
    actual=(out/'src/models/pocket_tts/acoustic_model.cpp').read_text()
    retained=(root/'.build/native-pocket-capacity-history-20260912-r1/src/models/pocket_tts/acoustic_model.cpp').read_text()
    assert actual==restore_frame_limit_fault(retained)
    r=json.loads((out/'binding.json').read_text())
    assert r['frame_limit_fault_required'] and not r['inference_validated']


@pytest.mark.parametrize('mutation',['false-complete','cut-tail','wrong-library'])
def test_independent_readback_refuses_false_green(tmp_path,mutation):
    source=Path(__file__).resolve().parents[1]/'deliverables/native-tts-completion-desktops-20260914-r1/linux-r1'
    work=tmp_path/'result';shutil.copytree(source,work)
    r=json.loads((work/'result.json').read_text());expected=r['library_sha256']
    binary_completion(work,expected)
    if mutation=='false-complete':
        r['observations'][1]['code']=0
        (work/'result.json').write_text(json.dumps(r))
    elif mutation=='cut-tail':
        pcm=work/'natural.f32';pcm.write_bytes(pcm.read_bytes()[:-4])
    else:expected='0'*64
    with pytest.raises(AssertionError):binary_completion(work,expected)
