"""The ASR selection gate refuses stale artifacts and false acoustic verdicts."""
import json

import pytest

from scripts.native_asr_checkpoint import verify_asr_proof
from scripts.native_checkpoint_binding import sha


@pytest.fixture
def proof(tmp_path):
    source = tmp_path / 'source'
    native_source = source / 'runtime/native_asr'; native_source.mkdir(parents=True)
    root = tmp_path / 'proof'; build = root / 'build'; build.mkdir(parents=True)
    library = build / 'libaii_native_asr.dylib'; library.write_bytes(b'fixture library')
    ort = tmp_path / 'ort.dylib'; ort.write_bytes(b'fixture runtime')
    expected = {'text': 'retained words', 'tokens': 2, 'trajectory': ['retained', 'retained words']}
    (source / 'reference.json').write_text(json.dumps({'short': expected}))
    (source / 'long-reference.json').write_text(json.dumps(expected))
    manifest = {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)}
                for p in source.glob('*.json')}
    (source / 'manifest.json').write_text(json.dumps(manifest))
    (build / 'CMakeCache.txt').write_text('CMAKE_HOME_DIRECTORY:INTERNAL=' + str(native_source) + '\n')
    ready = {'type': 'ready', 'runtime': 'fixture'}
    cancel = {'type': 'cancel', 'inference_entered': True, 'result_suppressed': True}
    transcript = {**expected, 'type': 'transcript', 'exhausted': True,
                  'model_padding': 10560, 'samples': 1059840, 'retained_peak_samples': 15937}
    (root / 'recognize.stdout').write_text('\n'.join(json.dumps(r) for r in
                                                   [ready, transcript, transcript, transcript, cancel]) + '\n')
    (root / 'recognize.stderr').write_text('')
    (root / 'contracts.stdout').write_text('100% tests passed, 0 tests failed out of 2\n')
    result = {'passed': True, 'platform': 'darwin',
              'stages': [{'name': n, 'exit_code': 0, 'retired': True}
                         for n in ('configure', 'build', 'contracts', 'recognize')],
              'native': {library.name: sha(library)}, 'source_manifest_sha256': sha(source / 'manifest.json'),
              'ort': {'runtime': str(ort), 'sha256': sha(ort)}, 'ready': ready, 'cancel': cancel}
    (root / 'result.json').write_text(json.dumps(result))
    return root, library, source, ort


def test_actual_library_and_acoustic_evidence_are_bound(proof):
    root, library, source, ort = proof
    bound = verify_asr_proof(root, library)
    for path in (library, ort, source / 'reference.json', root / 'recognize.stdout'):
        assert bound[str(path)] == sha(path)


@pytest.mark.parametrize('damage', ['library', 'source', 'ort', 'retirement', 'tokens', 'tail', 'buffer', 'contracts'])
def test_bad_asr_evidence_is_refused(proof, damage):
    root, library, source, ort = proof
    if damage in ('library', 'source', 'ort'):
        {'library': library, 'source': source / 'reference.json', 'ort': ort}[damage].write_bytes(b'changed')
    elif damage == 'retirement':
        p = root / 'result.json'; r = json.loads(p.read_text()); r['stages'][-1]['retired'] = False
        p.write_text(json.dumps(r))
    elif damage == 'contracts':
        (root / 'contracts.stdout').write_text('50% tests passed, 1 tests failed out of 2')
    else:
        p = root / 'recognize.stdout'; rows = [json.loads(s) for s in p.read_text().splitlines()]
        key, value = {'tokens': ('tokens', 999), 'tail': ('model_padding', 0),
                      'buffer': ('retained_peak_samples', 1059840)}[damage]
        rows[-2][key] = value
        p.write_text('\n'.join(json.dumps(r) for r in rows))
    with pytest.raises(AssertionError):
        verify_asr_proof(root, library)
