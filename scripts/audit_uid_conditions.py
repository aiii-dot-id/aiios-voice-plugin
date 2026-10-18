"""Fixed-condition native UID stress test, NOT held-out speaker qualification.

Two frozen public recordings are each compared with deterministic gain, channel,
noise, echo and duration variants. Reusing the source recording makes this a
stress test only: it cannot establish cross-utterance or cross-device accuracy.
It never changes the model, policy, registry or live enrollment.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import struct
import wave

from scripts.guided_capture_reference import embed


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def conditions(pcm):
    samples = struct.unpack('<' + 'h' * (len(pcm) // 2), pcm)
    rms = math.sqrt(sum(v*v for v in samples) / len(samples))
    rng = random.Random(714)
    variants = {
        'gain_0_2': [v * .2 for v in samples],
        'lowpass_5_samples': [sum(samples[max(0, i-4):i+1]) / min(i+1, 5)
                              for i in range(len(samples))],
        'noise_20db': [v + rng.gauss(0, rms * .1) for v in samples],
        'echo_80ms': [(v + (.35 * samples[i-1280] if i >= 1280 else 0)) / 1.35
                      for i, v in enumerate(samples)],
        'first_2_seconds': samples[:32000],
        'last_2_seconds': samples[-32000:],
    }
    return {name: struct.pack('<' + 'h' * len(values),
                             *(max(-32768, min(32767, round(x))) for x in values))
            for name, values in variants.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('panel', 'model', 'library', 'policy', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--model-sha256', required=True)
    parser.add_argument('--library-sha256', required=True)
    parser.add_argument('--activity', type=Path, required=True,
                        help='Frozen speaker-specific evidence spans, in panel case order')
    parser.add_argument('--activity-sha256', required=True)
    args = parser.parse_args()
    if digest(args.model) != args.model_sha256 or digest(args.library) != args.library_sha256:
        raise ValueError('Explicit model/library binding differs')
    policy = json.loads(args.policy.read_text())
    panel = json.loads(args.panel.read_text())
    if digest(args.activity) != args.activity_sha256:
        raise ValueError('Activity evidence binding differs')
    activity = [json.loads(line) for line in args.activity.read_text().splitlines()]
    if len(activity) != len(panel['cases']):
        raise ValueError('Activity and panel census differ')
    spans = {case['id']: row['evidence_spans'] for case, row in zip(panel['cases'], activity)}
    sources = [c for c in panel['cases'] if c['id'] in ('solo_a', 'solo_b')]
    if len(sources) != 2:
        raise ValueError('Two frozen solo sources required')
    bindings = {name: digest(getattr(args, name)) for name in ('panel', 'model', 'library', 'policy', 'activity')}
    recordings, profiles = [], []
    for case in sources:
        path = args.panel.parent / case['audio_file']
        if digest(path) != case['audio_sha256']:
            raise ValueError('Frozen audio binding differs')
        with wave.open(str(path), 'rb') as stream:
            if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (16000, 1, 2):
                raise ValueError('16 kHz mono PCM16 required')
            pcm = stream.readframes(stream.getnframes())
        if len(spans[case['id']]) != 1:
            raise ValueError('Solo case requires exactly one selected evidence span')
        span = spans[case['id']][0]
        start, end = span['start'], span['end']
        if not 0 <= start < end <= len(pcm)//2 or end-start < 32000:
            raise ValueError('Invalid selected evidence span')
        pcm = pcm[start*2:end*2]
        recordings.append(pcm)
        profiles.append(embed(args.library, args.model, pcm))
    rows = []
    for index, pcm in enumerate(recordings):
        for condition, changed in conditions(pcm).items():
            vector = embed(args.library, args.model, changed)
            scores = [sum(a*b for a, b in zip(vector, profile)) for profile in profiles]
            winner = max(range(2), key=scores.__getitem__)
            margin = scores[winner] - scores[1-winner]
            accepted = scores[winner] >= policy['threshold'] and margin >= policy['minimum_margin'] and margin > 0
            rows.append(dict(source=index, condition=condition, samples=len(changed)//2,
                             own_similarity=scores[index], other_similarity=scores[1-index],
                             accepted=accepted, correct=accepted and winner == index,
                             false_match=accepted and winner != index))
    if any(digest(getattr(args, key)) != value for key, value in bindings.items()):
        raise ValueError('Bound inputs changed during execution')
    result = dict(scope=__doc__, same_recording_stress_only=True, bindings=bindings,
                  threshold=policy['threshold'], minimum_margin=policy['minimum_margin'],
                  expected_cases=12, executed_cases=len(rows), cases=rows,
                  false_matches=sum(row['false_match'] for row in rows),
                  rejections=sum(not row['accepted'] for row in rows),
                  passed=len(rows) == 12 and all(row['correct'] for row in rows))
    with args.out.open('x') as output:
        output.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ('passed', 'executed_cases', 'false_matches', 'rejections')}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
