"""Differential native/Python enrollment codec; no inference or private profiles."""
import base64
import dataclasses
import json
import os
import random
import subprocess
from pathlib import Path
import numpy as np
import pytest
from runtime.speaker_identity.identity import Policy, canonical
from runtime.speaker_identity.snapshot import load_snapshot, dump_snapshot

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'deliverables/speaker-identity/installed-assets-20260910-r1/relocated-resources/uid-policy.json'

@pytest.fixture
def codec(tmp_path):
    binary = Path(os.environ.get('AII_UID_SNAPSHOT_PROBE', ROOT / '.build/native-uid-sdk-20260912-r1/aii_uid_snapshot_probe'))
    if not binary.is_file():
        pytest.skip('the native snapshot probe is not built; name it with AII_UID_SNAPSHOT_PROBE')
    def run(policy, raw=None):
        p = tmp_path / 'policy.json'; p.write_text(json.dumps(policy))
        args = [str(binary), str(p)]
        if raw is not None:
            s = tmp_path / 'snapshot.json'; s.write_bytes(raw); args.append(str(s))
        return subprocess.run(args, capture_output=True, timeout=5)
    return run

def test_policy_fingerprint_float_spelling(codec):
    p = json.loads(POLICY.read_text())
    rng = random.Random(712)
    values = [0, 1, -1, 0.0, -0.0, 1.0, -1.0, 1e-4, 1e-5, 1e-20, 5e-324] + [rng.uniform(-1, 1) for _ in range(300)]
    for value in values:
        p['threshold'] = value
        result = codec(p)
        assert result.returncode == 0, result.stderr
        assert result.stdout == canonical(p).encode(), value

def test_existing_frozen_snapshot_exact_bytes(codec):
    p = json.loads(POLICY.read_text())
    raw = (ROOT / 'deliverables/native-speaker-session-20260912-r3/existing-format.snapshot.json').read_bytes()
    result = codec(p, raw)
    assert result.returncode == 0, result.stderr
    assert result.stdout == raw

@pytest.mark.parametrize('label', ['Sam', 'Éponine', '聲音', '😀'*128, 'a"b\\c', r'literal\u0000', 'DEL\x7f', 'line\u2028separator'])
@pytest.mark.parametrize('revision', [1, 2**53+1, 2**63-1])
def test_labels_embeddings_revision_round_trip(codec, label, revision):
    p = json.loads(POLICY.read_text())
    v = np.zeros(256, dtype='<f8'); v[0] = 1; v[1] = -0.0
    body = {'policy': p, 'revision': revision, 'speakers': [{'id':'one','label':label,'samples':[{
        'audio_sha256':'a'*64, 'embedding_f64le_b64':base64.b64encode(v.tobytes()).decode()}]}]}
    raw = canonical(body).encode()
    with load_snapshot(raw, Policy(**p)) as store:
        assert dump_snapshot(store) == raw
    result = codec(p, raw)
    assert result.returncode == 0, result.stderr
    assert result.stdout == raw

@pytest.mark.parametrize('change', ['space','duplicate','negative','fraction','overflow','zero_revision','bad_base64','bad_norm','nul','empty_label','policy'])
def test_invalid_never_empty_enrollments(codec, change):
    p = json.loads(POLICY.read_text())
    v = np.zeros(256,dtype='<f8'); v[0]=1
    body={'policy':p,'revision':1,'speakers':[{'id':'one','label':'One','samples':[{'audio_sha256':'a'*64,'embedding_f64le_b64':base64.b64encode(v.tobytes()).decode()}]}]}
    if change=='negative':body['revision']=-1
    if change=='fraction':body['revision']=1.0
    if change=='overflow':body['revision']=2**63
    if change=='zero_revision':body['revision']=0
    if change=='bad_base64':body['speakers'][0]['samples'][0]['embedding_f64le_b64']='!'*2732
    if change=='bad_norm':body['speakers'][0]['samples'][0]['embedding_f64le_b64']=base64.b64encode(bytes(2048)).decode()
    if change=='nul':body['speakers'][0]['label']='One\0hidden'
    if change=='empty_label':body['speakers'][0]['label']=''
    raw=canonical(body).encode()
    if change=='space':raw+=b' '
    if change=='duplicate':raw=raw.replace(b'"revision":1',b'"revision":1,"revision":1')
    if change=='policy':raw=raw.replace(b'"threshold":0.56',b'"threshold":0.57')
    result=codec(p,raw)
    assert result.returncode != 0, change
    assert result.stdout == b''
