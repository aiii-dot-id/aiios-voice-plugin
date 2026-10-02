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
            match.get('match', {}).get('outcome') == 'known', 'restart acoustic match absent')
    if registry.get('confirmed_link_mechanics_only'):
        # A corrected canonical UUID can point to an unlabeled target. The
        # acoustic match must still name the pre-link profile; a display label
        # on the target is not a condition of speaker continuity.
        require(registry.get('confirmed_unlink_after_close') is True and
                match['match'].get('candidate_uuid') and
                match['match']['candidate_uuid'] != match['speaker_uuid'],
                'linked restart acoustic match absent')
    else:
        require(match.get('display_label'), 'restart acoustic label absent')


def verify(checkpoint, evidence, expected_sha256):
    checkpoint = checkpoint.resolve()
    require(sha(evidence) == expected_sha256, 'qualification evidence changed')
    proof = json.loads(evidence.read_text(encoding='utf-8-sig'))
    if 'proof_sha256' in proof:
        return verify_resident_audit(checkpoint, evidence, expected_sha256, proof)
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


def validate_resident_result(proof):
    """The expanded gate permits quiet abstention, never wrong/unstable identity."""
    require(proof.get('passed') is True and proof.get('installed') is False,
            'recorded resident qualification did not pass')
    require(not any(proof.get(k) for k in ('failure', 'error', 'cleanup_error')),
            'resident qualification contains errors')
    for key in ('expanded_acquisition', 'identity_lifecycle', 'solo_acquisition_passed',
                'identity_safety_passed', 'normal_recovery_passed', 'process_retired'):
        require(proof.get(key) is True, 'resident proof incomplete: '+key)
    require(type(proof.get('retirement_exit')) is int and proof['retirement_exit'] == 0,
            'resident retirement unresolved')
    duration = proof.get('seconds')
    require(type(duration) in (int, float) and math.isfinite(duration) and duration > 0,
            'resident duration missing')
    cases = proof.get('cases', [])
    require(len(cases) == 28 and len({c.get('label') for c in cases}) == 28,
            'expanded resident panel census differs')
    protocol = proof.get('complete_overlap_protocol', {})
    require(protocol.get('gains') == [[1, 1], [1, .25], [.25, 1]]
            and protocol.get('max_speaker_wer') == .35 and protocol.get('max_case_wer') == .25,
            'complete overlap protocol differs')
    mixtures = [c for c in cases if c['label'].startswith('mixture-')]
    require(len(mixtures) == 3 and any(c.get('interruption_recovery') is True for c in mixtures),
            'overlap interruption/recovery absent')
    for case in cases:
        # A solo, quiet, unknown or recovery case must record that its UUID
        # stayed stable: an omitted field has not shown it, so it never
        # defaults true. An overlap mixture has no single stable speaker; its
        # identity and text are graded below, and a stability it does record
        # must still be true.
        mixture = case['label'].startswith('mixture-')
        stable = case.get('stable', None) if mixture else case.get('stable')
        require((stable is True or (mixture and 'stable' not in case)) and bool(case.get('finals')),
                'unstable or unrecorded identity stability, or missing transcript')
        finals, observations = case['finals'], case.get('observations', [])
        require(len(finals) == len(observations), 'resident attribution census differs')
        for final in finals:
            joined = [o for o in observations if o.get('refers_to') == final.get('sequence')
                      and o.get('track_id') == final.get('track_id')]
            require(len(joined) == 1, 'resident attribution join differs')
            delay = joined[0]['elapsed']-final['elapsed']
            require(math.isfinite(delay) and 0 <= delay <= 3, 'resident attribution deadline missed')
    for case in mixtures:
        score = case.get('attribution', {})
        require(score.get('attribution_passed') is True and score.get('text_passed') is True,
                'complete overlap text/identity failed')


def verify_resident_audit(checkpoint, evidence, expected_sha256, audit):
    """Consume a hash-bound expanded proof; an audit's green summary is not enough."""
    require(audit.get('passed') is True and audit.get('installed') is False
            and audit.get('published') is False and audit.get('cases') == 28,
            'resident audit incomplete or scope differs')
    require(Path(audit['checkpoint']).resolve() == checkpoint, 'different audit checkpoint')
    original = Path(audit['proof'])
    require(sha(original) == audit['proof_sha256'], 'original resident proof changed')
    proof = json.loads(original.read_text())
    validate_resident_result(proof)
    frozen, _, _, required = verify_checkpoint(checkpoint)
    require(audit.get('runtime_manifest_sha256') == frozen['runtime_manifest_sha256']
            and audit.get('carrier_sha256') == frozen['carrier_sha256'], 'audit runtime/carrier differs')
    bound = proof.get('bindings', {})
    require(all(audit.get('bindings', {}).get(path) == digest and bound.get(path) == digest
                for path, digest in required.items()), 'resident checkpoint binding absent or changed')
    require(bool(bound) and all(sha(Path(path)) == digest for path, digest in bound.items()),
            'an original resident input changed')
    require(sha(original) == audit['proof_sha256'] and sha(evidence) == expected_sha256,
            'resident evidence changed during verification')
    return dict(passed=True, qualification_sha256=expected_sha256,
                runtime_manifest_sha256=frozen['runtime_manifest_sha256'],
                freeze_sha256=sha(checkpoint/'freeze.json'),
                scope='expanded recorded SDK signing admission only', installed=False,
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
