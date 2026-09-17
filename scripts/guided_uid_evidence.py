"""Explicit model-specific evidence for the unchanged guided SDK protocol.

This prevents comparing a new embedding space with the old model's reference.
It does not relax byte fidelity, matching thresholds, consent, or restart gates.
"""
import hashlib
import json
from pathlib import Path


def load_contract(path, uid, bind):
    contract = json.loads(bind(path).read_text())
    if contract['schema'] != 'aiii.voice.guided-uid-evidence':
        raise ValueError('guided UID evidence schema differs')
    if hashlib.sha256(Path(uid).read_bytes()).hexdigest() != contract['model_sha256']:
        raise ValueError('guided reference model differs')
    loaded = {}
    for name in ('assessment', 'embeddings', 'policy'):
        row = contract[name]
        source = bind(Path(row['path']))
        if hashlib.sha256(source.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('guided evidence changed: ' + name)
        loaded[name] = json.loads(source.read_text())
    proof, policy = loaded['assessment'], loaded['policy']
    stats = proof['guided_capture']
    if (proof['passed'] is not True or proof['production_changed'] is not False
            or proof['native_process_retired'] is not True
            or proof['native_parity_cases'] != 161
            or stats['known_trials'] != 60 or stats['unknown_trials'] != 65
            or stats['known_correct'] < 57 or stats['known_wrong'] != 0
            or stats['unknown_accepted'] != 0):
        raise ValueError('guided evidence acceptance failed')
    if (policy['embedding_binding'] != contract['embedding_binding']
            or policy['calibration_sha256'] != contract['assessment']['sha256']
            or policy['minimum_enrollment_samples'] != 1
            or policy['threshold'] != .56 or policy['minimum_margin'] != .105):
        raise ValueError('guided policy differs from the frozen thresholds/binding')
    assessed = dict(captures=[dict(pcm_sha256=contract['capture_pcm_sha256'])])
    return policy, Path(contract['assessment']['path']), assessed, Path(contract['embeddings']['path'])
