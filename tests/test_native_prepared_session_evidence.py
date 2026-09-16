"""The completed comparison must not become green by changing its summary."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / 'deliverables/native-prepared-session-windows-20260913-r1'


@pytest.mark.parametrize('damage', [None, 'false-green', 'lost-opening', 'false-stop', 'wrong-tts', 'unretired'])
def test_complete_gate_checks_raw_evidence_after_inventory_is_rehashed(tmp_path, damage):
    with zipfile.ZipFile(PROOF / 'windows-evidence.zip') as z:
        files = {name: z.read(name) for name in z.namelist()}
    result = json.loads(files['run/result.json'])
    if damage == 'false-green':
        result['performance_gate_passed'] = True
    elif damage in ('lost-opening', 'false-stop'):
        name = 'run/2-candidate/conversation-0/report.json'
        report = json.loads(files[name])
        if damage == 'lost-opening':
            report['transcript'] = report['transcript'][20:]
        else:
            receipt = next(r for r in report['receipts'] if r['observation']['outcome'] == 'stopped')
            receipt['observation']['outcome'] = 'drained'
        files[name] = json.dumps(report).encode()
        result['runs'][1]['conversations'][0]['sha256'] = hashlib.sha256(files[name]).hexdigest()
    elif damage == 'wrong-tts':
        result['profiles']['candidate']['libraries']['native_pocket_resident.dll'] = '0' * 64
    elif damage == 'unretired':
        retirement = json.loads(files['retirement.json'])
        retirement['absent_pids'].remove(result['runs'][1]['loaded_worker']['pid'])
        files['retirement.json'] = json.dumps(retirement).encode()
    files['run/result.json'] = json.dumps(result).encode()
    manifest = json.loads(files['manifest.json'])
    for name in manifest:
        manifest[name] = {'bytes': len(files[name]), 'sha256': hashlib.sha256(files[name]).hexdigest()}
    files['manifest.json'] = json.dumps(manifest).encode()
    evidence = tmp_path / 'evidence.zip'
    with zipfile.ZipFile(evidence, 'x', zipfile.ZIP_DEFLATED) as z:
        for name, raw in files.items():
            z.writestr(name, raw)
    output = tmp_path / 'audit.json'
    p = subprocess.run([
        sys.executable, '-m', 'scripts.audit_native_prepared_session',
        '--source', str(PROOF / 'transfer/source.zip'),
        '--evidence', str(evidence), '--out', str(output),
    ], cwd=ROOT, text=True, capture_output=True, timeout=30)
    if damage:
        assert p.returncode != 0 and 'AssertionError' in p.stderr, p.stdout + p.stderr
        assert not output.exists(), 'invalid evidence published a verdict'
    else:
        assert p.returncode == 0, p.stdout + p.stderr
        audit = json.loads(output.read_text())
        assert audit['passed'] and audit['startup_gate_passed']
        assert not audit['performance_gate_passed'] and not audit['tts_gate_passed']
        assert len(audit['barge_in']) == 8 and len(audit['exact_pcm']) == 4
