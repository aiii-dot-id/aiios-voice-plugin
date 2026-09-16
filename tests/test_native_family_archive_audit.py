"""Self-consistent bad bundles must fail semantic readback, not just hashes."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import pytest

ROOT = Path(__file__).resolve().parents[1]
FAMILY = ROOT / 'deliverables/checkpoints/cp3-native-desktop-family-20260913-r1'


@pytest.mark.parametrize('damage', [None, 'wrong-platform-binary', 'wrong-windows-model', 'missing-interface'])
def test_real_family_readback_and_consistently_rehashed_mutations(tmp_path, damage):
    h = lambda raw: hashlib.sha256(raw).hexdigest()
    handoff = json.loads((FAMILY / 'handoff.json').read_text())
    source = FAMILY / handoff['bundle']['bundle']
    root = source.name.removesuffix('.aiiospkg') + '/'
    with tarfile.open(source) as t:
        members = {m.name: t.extractfile(m).read() for m in t if m.isfile()}
    prefix = root + 'install-root/'
    manifest = json.loads(members[root + 'manifest.json'])
    if damage == 'wrong-platform-binary':
        win = next(v for v in manifest['variants'] if v['platform'] == 'windows')
        linux = next(v for v in manifest['variants'] if v['platform'] == 'linux')
        raw = members[prefix + linux['entrypoint']]
        members[prefix + win['entrypoint']] = raw; win['artifact_hash'] = 'sha256:' + h(raw)
    elif damage == 'wrong-windows-model':
        models = json.loads(members[prefix + 'models.json'])
        next(m for m in models if m['path'] == 'endpoint/windows/coefficients.f32')['sha256'] = 'a' * 64
        members[prefix + 'models.json'] = json.dumps(models).encode()
    elif damage == 'missing-interface':
        manifest['interfaces']['core'].pop()
    aggregate = hashlib.sha256()
    for n, raw in sorted(members.items()):
        if n.startswith(prefix): aggregate.update((n[len(prefix):] + '\0' + h(raw) + '\n').encode())
    manifest['package_hash'] = handoff['bundle']['package_hash'] = 'sha256:' + aggregate.hexdigest()
    view = {k: v for k, v in manifest.items() if k != 'package_hash'}
    handoff['bundle']['manifest_hash'] = 'sha256:' + h(json.dumps(view, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())
    members[root + 'manifest.json'] = json.dumps(manifest).encode()
    target = tmp_path / source.name
    with tarfile.open(target, 'w:gz') as t:
        for n, raw in sorted(members.items()):
            info = tarfile.TarInfo(n); info.size = len(raw); info.mode = 0o644
            t.addfile(info, io.BytesIO(raw))
    handoff['bundle'].update(sha256=h(target.read_bytes()), bytes=target.stat().st_size)
    (tmp_path / 'handoff.json').write_text(json.dumps(handoff))
    result = subprocess.run([sys.executable, '-m', 'scripts.audit_native_desktop_family',
                             '--family', str(tmp_path), '--out', str(tmp_path / 'audit.json')],
                            cwd=ROOT, capture_output=True, text=True, timeout=20)
    if damage:
        assert result.returncode != 0 and 'AssertionError' in result.stderr
        assert not (tmp_path / 'audit.json').exists(), 'failed readback wrote success'
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads((tmp_path / 'audit.json').read_text())['passed']
