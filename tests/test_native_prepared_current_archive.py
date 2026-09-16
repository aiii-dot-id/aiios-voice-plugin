"""An internally rehashed report cannot replace the recorded comparison."""
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from scripts.audit_native_prepared_current_session import audit

ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / 'deliverables/native-prepared-current-session-windows-20260914-r1'


@pytest.mark.parametrize('damage', (
    'none', 'wrong_asr', 'missing_uid_image', 'lost_words', 'false_ready',
    'false_timing', 'alive_owner', 'pcm', 'changed_tts', 'changed_model',
))
def test_rehashed_false_full_engine_proof_is_refused(tmp_path, damage):
    source = PROOF / 'transfer/source.zip'
    evidence = PROOF / 'windows-evidence.zip'
    if damage == 'none':
        assert audit(source, evidence)['passed']
        return
    with zipfile.ZipFile(evidence) as z:
        blobs = {n: z.read(n) for n in z.namelist()}
    original = json.loads(blobs['manifest.json'])
    result = json.loads(blobs['run/result.json'])
    if damage in ('wrong_asr', 'missing_uid_image'):
        loaded = result['runs'][1]['loaded_worker']
        if damage == 'wrong_asr':
            loaded['bound_images']['aii_native_asr.dll']['sha256'] = '0' * 64
        else:
            del loaded['bound_images']['aii_native_uid.dll']
        child = json.loads(blobs['run/2-candidate/result.json'])
        child['loaded_worker'] = loaded
        blobs['run/2-candidate/result.json'] = json.dumps(child).encode()
    elif damage == 'lost_words':
        name = 'run/2-candidate/conversation-0/report.json'
        report = json.loads(blobs[name])
        report['transcript'] = 'opening words lost'
        blobs[name] = json.dumps(report).encode()
        h = hashlib.sha256(blobs[name]).hexdigest()
        result['runs'][1]['conversations'][0]['sha256'] = h
        child = json.loads(blobs['run/2-candidate/result.json'])
        child['conversations'][0]['sha256'] = h
        blobs['run/2-candidate/result.json'] = json.dumps(child).encode()
    elif damage == 'false_ready':
        result['runs'][1]['ready_ms'] = 1
    elif damage == 'false_timing':
        result['performance_gate_passed'] = not result['performance_gate_passed']
    elif damage == 'alive_owner':
        retired = json.loads(blobs['retirement.json'])
        retired['absent_pids'].remove(result['owner_pid'])
        blobs['retirement.json'] = json.dumps(retired).encode()
    elif damage == 'changed_tts':
        result['profiles']['candidate']['libraries']['native_pocket_resident.dll'] = '0' * 64
    elif damage == 'changed_model':
        result['profiles']['candidate']['model_paths'][2] += '-unrelated-change'
    else:
        stream = result['runs'][1]['speech'][0]['stream']
        name = f'run/2-candidate/conversation-0/output-stream-{stream}.wav'
        pcm = bytearray(blobs[name])
        pcm[-1] ^= 1
        blobs[name] = bytes(pcm)
    blobs['run/result.json'] = json.dumps(result).encode()
    blobs['manifest.json'] = json.dumps({
        n: {'bytes': len(blobs[n]), 'sha256': hashlib.sha256(blobs[n]).hexdigest()}
        for n in original
    }).encode()
    path = tmp_path / 'false-proof.zip'
    with zipfile.ZipFile(path, 'x', zipfile.ZIP_DEFLATED) as z:
        for n, raw in blobs.items():
            z.writestr(n, raw)
    with pytest.raises(AssertionError):
        audit(source, path)
