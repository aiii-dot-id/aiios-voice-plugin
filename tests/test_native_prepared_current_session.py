import ast,copy,json,subprocess,sys,zipfile
from pathlib import Path, PureWindowsPath
import pytest
from scripts.stage_native_prepared_current_session import runner
from scripts.audit_native_prepared_current_session import composition, measured_gate, LIMITS
from scripts.audit_native_shared_scratch_session import ORDER,norm
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def staged(tmp_path_factory):
    out=tmp_path_factory.mktemp('prepared-current')/'transfer'
    p=subprocess.run([sys.executable,'-m','scripts.stage_native_prepared_current_session','--out',str(out)],cwd=ROOT,capture_output=True,text=True,timeout=30)
    assert p.returncode==0,p.stderr
    with zipfile.ZipFile(out/'source.zip') as z:return {n:z.read(n) for n in z.namelist()}

def test_frozen_owner_keeps_full_speech_child_and_requires_real_startup_gain(staged):
    c=json.loads(staged['session-contract.json']);assert c['limits']==LIMITS and c['order']==ORDER
    assert staged['scripts/qualify_native_candidate_windows.py']==runner(staged['scripts/owner-template.py'])
    def calls(raw):
        return [ast.dump(n) for n in ast.walk(ast.parse(raw)) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in ('child','timing_gate')]
    assert calls(staged['scripts/qualify_native_candidate_windows.py'])==calls(staged['scripts/owner-template.py'])
    assert not json.loads(staged['prepared-result.json'])['performance_gate_passed']

@pytest.mark.parametrize('damage',('none','tts','worker','uid','mel','other_model','carrier','different_asr','unbound'))
def test_current_worker_tts_and_non_asr_contract_cannot_drift(staged,damage):
    current=json.loads(staged['parent-result.json']);prepared=json.loads(staged['prepared-result.json']);c=json.loads(staged['session-contract.json'])
    b=current['profiles']['candidate'];candidate=copy.deepcopy(b);dest='C:/isolated/candidate-bin'
    candidate['worker']=str(PureWindowsPath(dest)/'aii_voice_worker.exe')
    candidate['model_paths'][0]=c['model_root'];candidate['libraries']['aii_native_asr.dll']=c['component_sha256']
    before={**b['libraries'],'aii_voice_worker.exe':current['copy']['after']['aii_voice_worker.exe']}
    after={**before,'aii_native_asr.dll':c['component_sha256']}
    inv={'source':str(PureWindowsPath(b['worker']).parent),'destination':dest,'before':before,'after':after}
    r={'profiles':{'baseline':b,'candidate':candidate},'copy':inv}
    bindings={norm(folder)+'/'+name:h for folder,mapping in ((inv['source'],before),(dest,after)) for name,h in mapping.items()}
    composition(current,prepared,r,c,bindings)
    if damage=='none':return
    if damage in ('tts','worker','different_asr'):
        after[{'tts':'native_pocket_resident.dll','worker':'aii_voice_worker.exe','different_asr':'aii_native_asr.dll'}[damage]]='0'*64
    elif damage=='uid':candidate['uid_model']+='-wrong'
    elif damage=='mel':candidate['model_paths'][1]+='-wrong'
    elif damage=='other_model':candidate['model_paths'][2]+='-wrong'
    elif damage=='carrier':candidate['carrier']+='-wrong'
    else:del bindings[norm(dest)+'/aii_native_asr.dll']
    with pytest.raises(AssertionError):composition(current,prepared,r,c,bindings)

@pytest.mark.parametrize('damage',('none','too_small_ratio','too_small_absolute','first','recovery','repeated','last_recovery','realtime'))
def test_startup_gain_cannot_hide_worse_conversation(damage):
    rows=[{'arm':arm,'ready_ms':10000 if arm=='baseline' else 8900,'speech':[{'first_pcm_ms':100,'rtf':.5} for _ in range(4)]} for arm in ORDER]
    if damage=='too_small_ratio':
        for r in rows:r['ready_ms']*=10
        for r in rows:
            if r['arm']=='candidate':r['ready_ms']=95000
    elif damage=='too_small_absolute':
        for r in rows:r['ready_ms']/=10
    elif damage in ('first','recovery','repeated','last_recovery'):
        pos=('first','recovery','repeated','last_recovery').index(damage)
        for r in rows:
            if r['arm']=='candidate':r['speech'][pos]['first_pcm_ms']=106
    elif damage=='realtime':
        for r in rows:
            for s in r['speech']:s['rtf']=1.1
    assert measured_gate(rows)['performance_gate_passed']==(damage=='none')
