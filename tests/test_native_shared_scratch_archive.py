"""Self-consistently rehashed false archives must not obtain acceptance."""
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from scripts.audit_native_shared_scratch_session import audit

ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / 'deliverables/native-shared-scratch-session-windows-20260914-r1'


@pytest.mark.parametrize('damage', ('none', 'wrong_dll', 'missing_uid_image', 'lost_words',
                                  'false_ready', 'false_timing', 'alive_owner', 'pcm'))
def test_rehashed_false_completed_proof_is_refused(tmp_path, damage):
    with zipfile.ZipFile(PROOF / 'windows-evidence.zip') as z:
        blobs = {n: z.read(n) for n in z.namelist()}
    original = json.loads(blobs['manifest.json'])
    result = json.loads(blobs['run/result.json'])
    if damage == 'none':
        assert audit(PROOF / 'transfer/source.zip', PROOF / 'windows-evidence.zip')['passed']
        return
    if damage in ('wrong_dll', 'missing_uid_image'):
        loaded = result['runs'][1]['loaded_worker']
        if damage == 'wrong_dll': loaded['bound_images']['native_pocket_resident.dll']['sha256'] = '0' * 64
        else: del loaded['bound_images']['aii_native_uid.dll']
        child = json.loads(blobs['run/2-candidate/result.json'])
        child['loaded_worker'] = loaded
        blobs['run/2-candidate/result.json'] = (json.dumps(child) + '\n').encode()
    elif damage == 'lost_words':
        name = 'run/2-candidate/conversation-0/report.json'
        report = json.loads(blobs[name]); report['transcript'] = 'opening words lost'
        blobs[name] = (json.dumps(report) + '\n').encode()
        h = hashlib.sha256(blobs[name]).hexdigest()
        result['runs'][1]['conversations'][0]['sha256'] = h
        child = json.loads(blobs['run/2-candidate/result.json'])
        child['conversations'][0]['sha256'] = h
        blobs['run/2-candidate/result.json'] = (json.dumps(child) + '\n').encode()
    elif damage == 'false_ready': result['runs'][1]['ready_ms'] = 1
    elif damage == 'false_timing': result['performance_gate_passed'] = not result['performance_gate_passed']
    elif damage == 'alive_owner':
        retired = json.loads(blobs['retirement.json']); retired['absent_pids'].remove(result['owner_pid'])
        blobs['retirement.json'] = json.dumps(retired).encode()
    else:
        stream = result['runs'][1]['speech'][0]['stream']
        name = f'run/2-candidate/conversation-0/output-stream-{stream}.wav'
        pcm = bytearray(blobs[name]); pcm[-1] ^= 1; blobs[name] = bytes(pcm)
    blobs['run/result.json'] = (json.dumps(result) + '\n').encode()
    blobs['manifest.json'] = json.dumps({n: {'bytes': len(blobs[n]), 'sha256': hashlib.sha256(blobs[n]).hexdigest()}
                                         for n in original}).encode()
    path = tmp_path / 'false-proof.zip'
    with zipfile.ZipFile(path, 'x', zipfile.ZIP_DEFLATED) as z:
        for n, raw in blobs.items(): z.writestr(n, raw)
    with pytest.raises(AssertionError): audit(PROOF / 'transfer/source.zip', path)
