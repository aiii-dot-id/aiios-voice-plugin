"""Offline private-profile replay of a confirmed correction, not identity proof.

Never mutates the input or a live registry. UUIDs, embeddings, paths and labels
are excluded from its result. Checks both original profiles, an ambiguous
midpoint, and undo through the production codec and matching functions.
"""
from scripts._assertions import require_assertions
require_assertions()
import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import uuid


def prove(raw, probe):
    root = json.loads(raw)
    snapshot = json.loads(root['profile_document'])
    profiles = snapshot['speakers']
    if len(profiles) != 2 or any(len(p['samples']) != 1 for p in profiles):
        raise ValueError('This bounded replay needs exactly two single-recording profiles')
    policy = json.dumps(snapshot['policy'], sort_keys=True, separators=(',', ':'))

    def call(document, operation, id, **extra):
        request = dict(document=document, policy=policy, expected_revision=json.loads(document)['revision'],
                       operation=operation, uuid=id, **extra)
        done = subprocess.run([str(probe)], input=json.dumps(request), text=True,
                              capture_output=True, timeout=20)
        if done.returncode:
            raise RuntimeError('Production registry refused offline proof; private output withheld')
        return json.loads(done.stdout)

    document = raw.decode()
    source, target = profiles[1]['id'], profiles[0]['id']
    linked = call(document, 'link', source, target_uuid=target)['document']
    assert json.loads(linked)['profile_document'] == root['profile_document']
    cases = []
    for profile in profiles:
        before = call(document, 'observe', str(uuid.uuid4()), sample=profile['samples'][0])
        after = call(linked, 'observe', str(uuid.uuid4()), sample=profile['samples'][0])
        assert before['match'] == after['match']
        assert before['uuid'] == profile['id'] and after['uuid'] == target
        assert after['document'] == linked
        cases.append(dict(accepted_before=True, canonical_after=True, match_unchanged=True))
    vectors = [struct.unpack('<256d', base64.b64decode(p['samples'][0]['embedding_f64le_b64'])) for p in profiles]
    midpoint = [a+b for a,b in zip(*vectors)]
    norm = sum(x*x for x in midpoint)**.5
    sample = dict(audio_sha256=hashlib.sha256(b'synthetic-midpoint-not-a-recording').hexdigest(),
                  embedding_f64le_b64=base64.b64encode(struct.pack('<256d', *(x/norm for x in midpoint))).decode())
    before = call(document, 'observe', str(uuid.uuid4()), sample=sample)
    after = call(linked, 'observe', str(uuid.uuid4()), sample=sample)
    assert before['match'] == after['match'] and after['match']['outcome'] == 'ambiguous'
    assert after['continuity'] == 'provisional'
    undone = call(linked, 'link', source, target_uuid=source)['document']
    restored = call(undone, 'observe', str(uuid.uuid4()), sample=profiles[1]['samples'][0])
    assert restored['uuid'] == source
    assert json.loads(undone)['profile_document'] == root['profile_document']
    return dict(passed=True, scope=__doc__, registry_sha256=hashlib.sha256(raw).hexdigest(),
                probe_sha256=hashlib.sha256(probe.read_bytes()).hexdigest(), cases=cases,
                ambiguous_midpoint_still_refused=True, undo_restores_uuid=True,
                acoustic_profiles_unchanged=True, live_registry_modified=False,
                proves_automatic_fragmentation_fixed=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('registry', 'probe', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    raw = args.registry.read_bytes()
    result = prove(raw, args.probe.resolve())
    if raw != args.registry.read_bytes():
        raise RuntimeError('Source registry changed during proof')
    with args.out.open('x') as output:
        output.write(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
