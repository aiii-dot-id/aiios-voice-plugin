"""Fixed-policy development comparison on disjoint public-speech chapters.

Recomputes embeddings with the explicitly bound native UID model. No historical
embeddings, threshold fitting, live registry mutation or qualification claim.
One and three independent references are compared with the same five queries
per speaker. Removing the correct reference tests rejection, not an independent
unknown-speaker population. Audio, vectors and reference text are not emitted.
"""
import argparse
import json
from pathlib import Path
import time
import wave

from scripts.audit_uid_conditions import digest
from scripts.guided_capture_reference import embed, unit


def partition(panel, extra):
    if len(panel) != 180 or len(extra) != 60:
        raise ValueError('Expected 180 panel and 60 additional reference recordings')
    rows = panel + extra
    if len({r['id'] for r in rows}) != 240 or len({r['audio_sha256'] for r in rows}) != 240:
        raise ValueError('Duplicate recording or identifier')
    references, queries, speakers = [], [], set()
    for index in range(30):
        group, additions = panel[index*6:index*6+6], extra[index*2:index*2+2]
        speaker, chapter = group[0]['speaker_id'], group[0]['chapter_id']
        if speaker in speakers or any(r['speaker_id'] != speaker for r in group + additions):
            raise ValueError('Speaker grouping differs')
        if any(r['chapter_id'] == chapter for r in group[1:]) or any(r['chapter_id'] != chapter for r in additions):
            raise ValueError('Reference/query chapters overlap')
        speakers.add(speaker)
        references.append([group[0]['id'], *[r['id'] for r in additions]])
        queries.extend((index, r['id']) for r in group[1:])
    return references, queries


def decision(scores, threshold, minimum_margin):
    ordered = sorted(enumerate(scores), key=lambda item: (-item[1], item[0]))
    winner, score = ordered[0]
    margin = score - ordered[1][1]
    return winner, score, margin, score >= threshold and margin >= minimum_margin and margin > 0


def evaluate(vectors, references, queries, count, policy, aggregation='centroid'):
    centers = [unit([sum(vectors[key][j] for key in keys[:count])/count
                     for j in range(256)]) for keys in references]
    rows = []
    for owner, key in queries:
        scores = [max(-1., min(1., sum(a*b for a, b in zip(vectors[key], center)))) for center in centers]
        if aggregation=='max_exemplar':
            scores=[max(max(-1.,min(1.,sum(a*b for a,b in zip(vectors[key],vectors[ref]))))
                        for ref in keys[:count]) for keys in references]
        winner, score, margin, accepted = decision(scores, policy['threshold'], policy['minimum_margin'])
        absent = [s for i, s in enumerate(scores) if i != owner]
        unknown_accepted = decision(absent, policy['threshold'], policy['minimum_margin'])[3]
        rows.append(dict(owner_index=owner, query_id=key, best_index=winner, score=score,
                         margin=margin, own_similarity=scores[owner], accepted=accepted,
                         correct=accepted and winner == owner, false_match=accepted and winner != owner,
                         correct_reference_removed_accept=unknown_accepted))
    return dict(reference_count=count, aggregation=aggregation, query_count=len(rows), cases=rows,
                correct=sum(r['correct'] for r in rows), false_matches=sum(r['false_match'] for r in rows),
                rejections=sum(not r['accepted'] for r in rows),
                correct_reference_removed_accepts=sum(r['correct_reference_removed_accept'] for r in rows))


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('panel', 'extra', 'panel-audio', 'extra-audio', 'model', 'library', 'policy', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('panel', 'extra', 'model', 'library', 'policy'):
        parser.add_argument('--' + name + '-sha256', required=True)
    args = parser.parse_args()
    bindings = {}
    for name in ('panel', 'extra', 'model', 'library', 'policy'):
        bindings[name] = digest(getattr(args, name))
        if bindings[name] != getattr(args, name + '_sha256'):
            raise ValueError('Explicit input binding differs: ' + name)
    panel, extra = [json.loads(path.read_text())['samples'] for path in (args.panel, args.extra)]
    references, queries = partition(panel, extra)
    policy = json.loads(args.policy.read_text())
    paths = {}
    for rows, root in ((panel, args.panel_audio), (extra, args.extra_audio)):
        for row in rows:
            path = (root / row['audio_file']).resolve()
            if not path.is_relative_to(root.resolve()) or digest(path) != row['audio_sha256']:
                raise ValueError('Recording binding differs')
            paths[row['id']] = path
    sources = [Path(__file__), Path(__file__).with_name('guided_capture_reference.py'),
               Path(__file__).with_name('audit_uid_conditions.py')]
    protocol = dict(scope=__doc__, bindings=bindings,
                    source={p.name:digest(p) for p in sources},
                    audio={r['id']:r['audio_sha256'] for r in panel + extra},
                    references=references, queries=queries, threshold=policy['threshold'],
                    minimum_margin=policy['minimum_margin'], arms=['1','3','3-max-exemplar'],
                    expected_recordings=240, expected_queries_per_arm=150)
    args.out.mkdir(parents=True, exist_ok=False)
    save(args.out / 'protocol.json', protocol)  # Frozen before first inference.
    result = dict(completed=False, qualified=False, elapsed_seconds=0,
                  scope='Development-only cross-utterance component comparison; not overlap, device or installed qualification',
                  protocol_sha256=digest(args.out / 'protocol.json'))
    started = time.monotonic()
    try:
        vectors = {}
        for index, (key, path) in enumerate(paths.items()):
            if time.monotonic() - started > 1200:
                raise TimeoutError('Bounded component comparison exceeded 20 minutes')
            with wave.open(str(path), 'rb') as audio:
                if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
                    raise ValueError('16 kHz mono PCM16 required')
                pcm = audio.readframes(audio.getnframes())
            vectors[key] = embed(args.library, args.model, pcm)
            if index % 30 == 29:
                print(json.dumps(dict(recordings=index+1, total=240, elapsed_seconds=time.monotonic()-started)), flush=True)
        result['arms'] = {str(n):evaluate(vectors, references, queries, n, policy) for n in (1, 3)}
        result['arms']['3-max-exemplar']=evaluate(vectors,references,queries,3,policy,'max_exemplar')
        if any(digest(getattr(args, name)) != value for name, value in bindings.items()) or any(digest(p) != protocol['source'][p.name] for p in sources) or any(digest(paths[key]) != value for key, value in protocol['audio'].items()):
            raise ValueError('Bound inputs changed during execution')
        result['completed'] = True
    except Exception as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        result['elapsed_seconds'] = time.monotonic() - started
        save(args.out / 'result.json', result)
        print(json.dumps({**{k:v for k,v in result.items() if k != 'arms'},
                          'arms':{k:{n:v for n,v in arm.items() if n != 'cases'} for k,arm in result.get('arms', {}).items()}}))


if __name__ == '__main__':
    main()
