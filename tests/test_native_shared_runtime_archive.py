"""Exercise the runtime auditor against rehashed copies, never alter evidence."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / 'deliverables/native-shared-scratch-runtime-windows-20260914-r1'


@pytest.mark.parametrize('damage', ('none','image','unknown_speaker','lost_words','settings_audio','alive','file_count'))
def test_self_consistent_false_runtime_proof(tmp_path, damage):
    with zipfile.ZipFile(PROOF/'windows-evidence.zip') as z:
        blobs = {n: z.read(n) for n in z.namelist()}
    manifest = json.loads(blobs['manifest.json'])
    def get(n): return json.loads(blobs['run/'+n])
    def put(n, v): blobs['run/'+n] = (json.dumps(v)+'\n').encode()
    if damage in ('image','file_count'):
        frozen = get('checkpoint/freeze.json')
        if damage == 'image': frozen['library_hashes']['native_pocket_resident.dll'] = '0'*64
        else: frozen['runtime_files'] -= 2
        put('checkpoint/freeze.json', frozen)
    elif damage == 'unknown_speaker':
        r = get('sdk/result.json'); r['unknown_observation']['decision'] = 'known'; put('sdk/result.json',r)
    elif damage == 'lost_words':
        r = get('sdk/spoken-regression/report.json'); r['transcript'] = 'opening words lost'; put('sdk/spoken-regression/report.json',r)
        e = get('sdk/result.json'); e['spoken_regression_sha256'] = hashlib.sha256(blobs['run/sdk/spoken-regression/report.json']).hexdigest(); put('sdk/result.json',e)
    elif damage == 'settings_audio':
        case = get('settings/result.json')['cases'][0]['name']; name = 'run/settings/'+case+'.wav'
        raw = bytearray(blobs[name]); raw[-1] ^= 1; blobs[name] = bytes(raw)
    elif damage == 'alive':
        r = json.loads(blobs['retirement.json']); r['absent_pids'].remove(get('sdk/result.json')['loaded_worker']['pid'])
        blobs['retirement.json'] = json.dumps(r).encode()
    blobs['manifest.json'] = json.dumps({n:{'bytes':len(blobs[n]),'sha256':hashlib.sha256(blobs[n]).hexdigest()} for n in manifest}).encode()
    archive = tmp_path/'changed.zip'; out = tmp_path/'audit.json'
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
        for n,raw in blobs.items(): z.writestr(n,raw)
    run = subprocess.run([sys.executable,'-m','scripts.audit_native_candidate_runtime',
                          '--source',str(PROOF/'transfer/source.zip'),'--evidence',str(archive),'--out',str(out)],
                         cwd=ROOT,capture_output=True,text=True,timeout=30)
    report = json.loads(out.read_text())
    if damage == 'none':
        assert run.returncode == 0, run.stderr
        assert report['passed'] and report['metadata_consistent']
    elif damage == 'file_count':
        assert run.returncode == 0 and report['passed']
        assert not report['metadata_consistent'] and report['metadata_problems'] and not report['ready_for_packaging']
    else:
        assert run.returncode != 0 and not report['passed'] and report['error']
