"""Self-consistently rehashed false evidence must still fail the semantic audit."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

import pytest

from scripts.audit_native_process_idle import main

DELIVERABLES=Path(__file__).resolve().parents[1]/'deliverables'


@pytest.mark.parametrize('damage',['cpu','native-image','false-stop','timing-verdict'])
@pytest.mark.parametrize('name',['native-process-idle-windows-20260913-r1',
                                 'native-worker-scratch-windows-20260913-r1'])
def test_semantic_falsehood_is_not_rescued_by_valid_archive_hashes(tmp_path,monkeypatch,damage,name):
    root=DELIVERABLES/name
    with zipfile.ZipFile(root/'windows-evidence.zip') as z:
        files={n:z.read(n) for n in z.namelist() if n!='manifest.json'}
    r=json.loads(files['run/result.json']);row=r['runs'][1]
    def put(n,v):files[n]=(json.dumps(v,indent=2)+'\n').encode()
    if damage=='cpu':
        row['process_windows'][0]['summary']['process_mean_cores']=0
    elif damage=='native-image':
        row['loaded_worker']['native_images']['aii_voice_worker.exe']['sha256']='0'*64
    elif damage=='false-stop':
        name='run/2-candidate/conversation-0/report.json';report=json.loads(files[name])
        stopped=next(x for x in report['receipts'] if x['observation']['outcome']=='stopped')
        stopped['observation']['outcome']='drained'
        put(name,report);row['conversations'][0]['sha256']=hashlib.sha256(files[name]).hexdigest()
    else:
        # One archive really failed and the other really passed. Neither verdict
        # may be trusted after changing it and recomputing the outer hashes.
        r['performance_gate_passed']=not r['performance_gate_passed']
        r['gates']['passed']=r['performance_gate_passed']
    put('run/2-candidate/result.json',row);put('run/result.json',r)
    manifest={n:{'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} for n,v in files.items()}
    target=tmp_path/'bad-evidence.zip'
    with zipfile.ZipFile(target,'x',zipfile.ZIP_DEFLATED) as z:
        for n,v in files.items():z.writestr(n,v)
        z.writestr('manifest.json',json.dumps(manifest))
    out=tmp_path/'audit.json'
    monkeypatch.setattr(sys,'argv',['audit','--source',str(root/'transfer/source.zip'),
                                    '--evidence',str(target),'--out',str(out)])
    with pytest.raises(AssertionError):main()
    assert not json.loads(out.read_text())['passed']
