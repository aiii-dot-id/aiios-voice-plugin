import copy
import json
from pathlib import Path
from pathlib import PureWindowsPath
import subprocess
import sys
import zipfile

import pytest

from scripts.derive_native_candidate_checkpoint import composition

ROOT=Path(__file__).resolve().parents[1]


def recorded():
    with zipfile.ZipFile(ROOT/'deliverables/native-family-windows-20260913-r2/windows-evidence.zip') as z:
        f=json.loads(z.read('run/checkpoint/freeze.json'))
        p=json.loads(z.read('run/checkpoint/runtime/voice-runtime.json'))
    with zipfile.ZipFile(ROOT/'deliverables/native-worker-scratch-windows-20260913-r1/windows-evidence.zip') as z:
        c=json.loads(z.read('run/result.json'))
    return f,p,c


def test_exact_tested_composition_not_the_older_packaged_asr():
    f,p,c=recorded();before=copy.deepcopy((f,p,c))
    candidate,images,changed=composition(f,p,c)
    assert changed=={'bin/aii_voice_worker.exe','bin/aii_native_asr.dll'}
    assert len(images)==14 and candidate['backend']=='vulkan'
    assert images['aii_native_asr.dll'].startswith('675b1b083fef')
    assert (f,p,c)==before


@pytest.mark.parametrize('damage', ['none','old-kind','component','unbound','sdk','policy','regression'])
def test_shared_tts_derivation_requires_the_exact_three_image_delta(damage):
    f,p,_=recorded()
    with zipfile.ZipFile(ROOT/'deliverables/native-shared-scratch-session-windows-20260914-r1/windows-evidence.zip') as z:
        c=json.loads(z.read('run/result.json'))
    _,images,changed=composition(f,p,c,'shared-tts')
    assert changed=={'bin/aii_voice_worker.exe','bin/aii_native_asr.dll','bin/native_pocket_resident.dll'}
    assert images['native_pocket_resident.dll']=='2d2f8460bc09b41ccfb3696f6063911e015802b182ac8242a16c28c7b3c09213'
    if damage=='none':return
    kind='worker-asr' if damage=='old-kind' else 'shared-tts'
    if damage=='component':c['contract']['component_sha256']='0'*64
    elif damage=='unbound':c['copy']['after']['aii_voice_worker.exe']='0'*64
    elif damage=='sdk':c['profiles']['candidate']['sdk_revision']='other-sdk'
    elif damage=='policy':p['files']['resources/uid-policy.json']['sha256']='0'*64
    elif damage=='regression':c['performance_gate_passed']=False
    with pytest.raises(ValueError):composition(f,p,c,kind)


def test_runtime_count_includes_inventory_and_carrier():
    f,p,_=recorded()
    assert f['runtime_files']==len(p['files'])+2
    source=(ROOT/'scripts/derive_native_candidate_checkpoint.py').read_text()
    assert "'runtime_files': len(updated['files']) + 2" in source


@pytest.mark.parametrize('damage',['model','policy','worker','closure','sdk','old-asr','failed'])
def test_composition_refuses_incomplete_or_different_candidate(damage):
    f,p,c=recorded();candidate=c['profiles']['candidate']
    if damage=='model':f['models']['stt/encoder.onnx']['sha256']='0'*64
    elif damage=='policy':p['files']['resources/uid-policy.json']['sha256']='0'*64
    elif damage=='worker':c['copy']['after']['aii_voice_worker.exe']='0'*64
    elif damage=='closure':candidate['libraries'].pop('aii_native_uid.dll')
    elif damage=='sdk':candidate['sdk_revision']='other-sdk'
    elif damage=='failed':c['performance_gate_passed']=False
    else:
        name='aii_native_asr.dll';old=f['library_hashes'][name];candidate['libraries'][name]=old
        path=PureWindowsPath(candidate['worker']).parent/name
        c['bindings'][str(path)]=old
    with pytest.raises(ValueError):composition(f,p,c)


@pytest.mark.parametrize('kind', ('worker-asr','shared-tts'))
def test_frozen_acceptance_retains_original_enrollment_and_thirteen_voice_cases(tmp_path,kind):
    out=tmp_path/'transfer'
    subprocess.run([sys.executable,'-m','scripts.stage_native_candidate_qualification','--out',str(out),'--composition-kind',kind],
                   cwd=ROOT,check=True,capture_output=True,timeout=30)
    with zipfile.ZipFile(out/'source.zip') as new, zipfile.ZipFile(ROOT/'deliverables/native-family-windows-20260913-r2/transfer/source.zip') as old:
        for name in ('scripts/prove_native_session_enrollment.py','scripts/prove_native_operator_settings.py',
                     'scripts/native_checkpoint_binding.py','scripts/package_native_runtime.py'):
            assert new.read(name)==old.read(name)
        for name in json.loads(old.read('manifest.json')):
            if name.startswith('plugin/native/'):assert new.read(name)==old.read(name)
        runner=new.read('scripts/qualify_native_candidate_windows.py').decode()
        assert "run('enrollment'" in runner and "run('settings'" in runner
        assert "len(settings['cases'])==13" in runner
        assert 'spoken_interruption_opening_words_and_recovery_passed' in runner
        assert '--checkpoint' in runner and 'cmake' not in runner
        assert json.loads(new.read('candidate-contract.json'))['composition_kind']==kind


@pytest.mark.parametrize('damage',[None,'file-count','bytes'])
def test_metadata_audit_does_not_hide_the_two_omitted_files(damage):
    from scripts.audit_native_candidate_runtime import inventory_metadata
    with zipfile.ZipFile(ROOT/'deliverables/native-family-windows-20260913-r2/windows-evidence.zip') as z:
        frozen=json.loads(z.read('run/checkpoint/freeze.json'))
        raw=z.read('run/checkpoint/runtime/voice-runtime.json');profile=json.loads(raw)
        build=json.loads(z.read('run/checkpoint/carrier-build.json'))
    if damage=='file-count':frozen['runtime_files']-=2
    if damage=='bytes':
        frozen['runtime_bytes']-=1
        with pytest.raises(AssertionError):inventory_metadata(frozen,profile,build,raw)
    else:
        okay,count,size=inventory_metadata(frozen,profile,build,raw)
        assert okay==(damage is None) and count==20 and size>0
