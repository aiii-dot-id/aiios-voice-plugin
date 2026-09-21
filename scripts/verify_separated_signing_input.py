"""Admit a measured, exact-byte separated-hearing checkpoint for signing.

This gate consumes the recorded SDK proof without converting it to installed,
browser, broad UID or release qualification. It does not sign or modify files.
Run on the machine retaining the original checkpoint and evidence inputs.
"""
import argparse
import json
import math
from pathlib import Path

from scripts.native_checkpoint_binding import sha, verify_checkpoint

CASES = ('solo_a', 'solo_b', 'alternating', 'overlap_equal',
         'overlap_b_quiet', 'overlap_a_quiet', 'overlap_cold')


def require(value, message):
    if not value:
        raise ValueError(message)


def validate_result(proof):
    require(proof.get('passed') is True, 'qualification did not pass')
    require(not any(proof.get(k) for k in ('error', 'cleanup_error',
            'registry_broker_error', 'broker_errors')), 'qualification contains errors')
    require(proof.get('process_retired') is True and
            type(proof.get('exit_code')) is int and proof['exit_code'] == 0 and
            proof.get('registry_broker_retired') is True, 'retirement unresolved')
    duration = proof.get('elapsed_seconds')
    require(type(duration) in (int, float) and math.isfinite(duration) and duration > 0,
            'qualification duration missing')
    registry = proof.get('registry', {})
    for key in ('resident_uuid_path', 'post_session_naming',
                'process_restart_qualified', 'confirmed_forget_after_close', 'test_host_only'):
        require(registry.get(key) is True, 'registry proof incomplete: '+key)
    require(proof.get('installed') is False and proof.get('persistent_uid_qualified') is False,
            'recorded SDK proof must retain its qualification limits')
    cases = proof.get('cases', [])
    require([row.get('case') for row in cases] == list(CASES), 'panel census differs')
    score = proof.get('score', {})
    require(score.get('passed') is True and set(score.get('cases', {})) == set(CASES),
            'panel score missing')
    for row in cases:
        require(score['cases'][row['case']].get('passed') is True, 'case score failed')
        require(row.get('finals') and len(row.get('observations', [])) == len(row['finals']),
                'attribution census differs')
        require(row.get('terminal', {}).get('status') == 'completed', 'case did not finish')
        for key, outcome in (('interruption', 'stopped'), ('recovery', 'drained')):
            receipt = row.get(key, {})
            observed = receipt.get('observation', {})
            require(receipt.get('result', {}).get('accepted') is True and
                    observed.get('terminal') is True and observed.get('outcome') == outcome and
                    observed.get('delivered_samples', 0) > 0,
                    key+' receipt incomplete')
    match = registry.get('acoustic_match_after_restart', {})
    require(match.get('speaker_uuid') and match.get('continuity') == 'matched' and
            match.get('display_label'), 'restart acoustic match absent')


def verify(checkpoint, evidence, expected_sha256):
    checkpoint = checkpoint.resolve()
    require(sha(evidence) == expected_sha256, 'qualification evidence changed')
    proof = json.loads(evidence.read_text(encoding='utf-8-sig'))
    validate_result(proof)
    require(Path(proof['checkpoint']).resolve() == checkpoint, 'different checkpoint')
    frozen, _, pin, required = verify_checkpoint(checkpoint)
    require(proof.get('runtime_manifest_sha256') == frozen['runtime_manifest_sha256'] and
            proof.get('sdk_revision') == pin['revision'], 'runtime or SDK binding differs')
    scripts = Path(__file__).resolve().parent
    for name in ('prove_sdk_separated_hearing.py', 'speaker_registry_test_host.py'):
        required[str(scripts/name)] = sha(scripts/name)
    bound = proof.get('bindings', {})
    require(all(bound.get(path) == digest for path, digest in required.items()),
            'required checkpoint or harness binding absent or changed')
    for path, digest in bound.items():
        require(sha(Path(path)) == digest, 'an original qualification input changed')
    require(sha(evidence) == expected_sha256, 'qualification changed during verification')
    return dict(passed=True, qualification_sha256=expected_sha256,
                runtime_manifest_sha256=frozen['runtime_manifest_sha256'],
                freeze_sha256=sha(checkpoint/'freeze.json'),
                scope='recorded SDK signing admission only', installed=False,
                signed=False, published=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--evidence-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.checkpoint, args.evidence, args.evidence_sha256)))


if __name__ == '__main__':
    main()
