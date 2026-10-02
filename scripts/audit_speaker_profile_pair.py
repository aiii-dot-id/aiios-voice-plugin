"""Replay two retained profiles through the production anonymous matcher.

Private offline diagnostic, not an acoustic accuracy score. The inputs are
already-extracted embeddings; no claim about their speaker identity or purity
can be made here. Neither the input registry nor the live identity is modified.
Only redacted decisions are written; biometric vectors and UUIDs stay private.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import uuid


def replay(registry: bytes, probe: Path, reference_index: int, query_index: int):
    root = json.loads(registry)
    snapshot = json.loads(root['profile_document'])
    profiles = snapshot['speakers']
    if reference_index == query_index or min(reference_index, query_index) < 0:
        raise ValueError('Choose two different nonnegative profile indices')
    reference, query = profiles[reference_index], profiles[query_index]
    if len(reference['samples']) != 1 or len(query['samples']) != 1:
        raise ValueError('This replay requires two single-recording profiles')
    policy = json.dumps(snapshot['policy'], sort_keys=True, separators=(',', ':'))
    document = None
    revision = 0
    results = []
    for sample in (reference['samples'][0], query['samples'][0]):
        request = dict(operation='observe', policy=policy, document=document,
                       expected_revision=revision, uuid=str(uuid.uuid4()), sample=sample)
        run = subprocess.run([str(probe)], input=json.dumps(request), text=True,
                             capture_output=True, timeout=20)
        if run.returncode:
            # The subprocess can contain private inputs; do not echo it.
            raise RuntimeError('Production matcher refused the private replay')
        reply = json.loads(run.stdout)
        document, revision = reply['document'], reply['revision']
        match = reply['match']
        results.append(dict(continuity=reply['continuity'], diagnostic={
            key: match[key] for key in ('outcome', 'reason', 'candidate_count',
                'score', 'margin', 'threshold', 'minimum_margin', 'profile_revision')
            if key in match}))
    return dict(scope='Retained embedding replay, not acoustic or identity accuracy',
                registry_sha256=hashlib.sha256(registry).hexdigest(),
                probe_sha256=hashlib.sha256(probe.read_bytes()).hexdigest(),
                reference_index=reference_index, query_index=query_index,
                decisions=results, source_registry_modified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, required=True)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--reference-index', type=int, required=True)
    parser.add_argument('--query-index', type=int, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    raw = args.registry.read_bytes()
    result = replay(raw, args.probe.resolve(), args.reference_index, args.query_index)
    if args.registry.read_bytes() != raw:
        raise RuntimeError('Registry changed during replay; evidence is not stable')
    # Exclusive creation; never overwrite an earlier finding or input.
    with args.out.open('x') as output:
        output.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
