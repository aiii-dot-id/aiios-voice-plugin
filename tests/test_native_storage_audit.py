"""Late resource validation must revoke every success bit already computed."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def test_invalid_gpu_evidence_cannot_leave_a_passed_report(tmp_path):
    prior=ROOT/'deliverables/native-tts-bounded-views-windows-20260913-r1'
    with zipfile.ZipFile(prior/'windows-evidence.zip') as z:
        members={n:z.read(n) for n in z.namelist()}
    name='run/2-candidate-gpu-before.stdout'
    members[name]=b'unrelated adapter; not this observation\n'
    manifest=json.loads(members['manifest.json'])
    manifest[name]={'bytes':len(members[name]),'sha256':hashlib.sha256(members[name]).hexdigest()}
    members['manifest.json']=json.dumps(manifest).encode()
    evidence=tmp_path/'bad-resource.zip'
    with zipfile.ZipFile(evidence,'x') as z:
        for name,raw in members.items():z.writestr(name,raw)
    result=tmp_path/'audit.json'
    run=subprocess.run([sys.executable,'-m','scripts.audit_native_single_prepare','--source',str(prior/'transfer/source.zip'),'--evidence',str(evidence),'--out',str(result)],cwd=ROOT,capture_output=True,text=True)
    assert run.returncode!=0
    report=json.loads(result.read_text())
    assert 'error' in report
    assert report['passed'] is False,'late failed audit still advertises passed'
    assert report['measured']['performance_gate_passed'] is False
