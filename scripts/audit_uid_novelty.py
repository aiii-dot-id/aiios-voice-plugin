"""Bounded, source-bound open-set speaker audit; never changes a live registry.

The first 15 panel speakers are development data and the last 15 are held
out. Within each half, the first ten supply a one-recording gallery and the
last five are genuinely absent speakers. Each speaker's five query recordings
come from a different chapter. The challenger threshold is selected ONLY on
the development half, under a zero observed false-accept constraint. This is
component research, not a claim of installed or human-level UID reliability.
No audio, transcript, embedding, speaker name or utterance identifier is
written to the result.
"""
import argparse
import ctypes as C
from itertools import combinations
import json
import math
from pathlib import Path
import time
import wave

import numpy as np
import onnxruntime as ort

from scripts.audit_uid_conditions import digest
from scripts.guided_capture_reference import embed


def cosine(a, b):
    return max(-1., min(1., sum(x*y for x, y in zip(a, b))))


def unit_any(values):
    norm = math.sqrt(sum(x*x for x in values))
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError('Invalid speaker vector norm')
    return [x/norm for x in values]


def score(query, gallery, vectors):
    ordered = sorted(((cosine(vectors[query], vectors[ref]), owner)
                      for owner, ref in gallery), key=lambda item:(-item[0],item[1]))
    best, owner = ordered[0]
    return best, best - ordered[1][0], owner


def accepted(scored, threshold, margin):
    return scored[0] >= threshold and scored[1] >= margin and scored[1] > 0


def evaluate(vectors, references, queries, begin, threshold, margin):
    gallery = [(owner, references[owner][0]) for owner in range(begin, begin+10)]
    known = [entry for entry in queries if begin <= entry[0] < begin+10]
    unknown = [entry for entry in queries if begin+10 <= entry[0] < begin+15]
    known_scores = [(owner, key, score(key, gallery, vectors)) for owner, key in known]
    unknown_scores = [(owner, key, score(key, gallery, vectors)) for owner, key in unknown]
    known_correct = sum(accepted(s, threshold, margin) and s[2] == owner
                        for owner, _, s in known_scores)
    known_wrong = sum(accepted(s, threshold, margin) and s[2] != owner
                      for owner, _, s in known_scores)
    unknown_false = sum(accepted(s, threshold, margin) for _, _, s in unknown_scores)
    novelty_ceiling = max(-1., threshold-margin/2)
    def pair_count(rows):
        events = 0
        guarded_events = 0
        for owner in sorted({entry[0] for entry in rows}):
            samples = [(key, s) for o, key, s in rows if o == owner]
            for (first, a), (second, b) in combinations(samples, 2):
                if (not accepted(a, threshold, margin) and
                        not accepted(b, threshold, margin) and
                        cosine(vectors[first], vectors[second]) >= threshold):
                    events += 1
                    if a[0] < novelty_ceiling and b[0] < novelty_ceiling:
                        guarded_events += 1
        return events, guarded_events
    duplicate, guarded_duplicate = pair_count(known_scores)
    novel, guarded_novel = pair_count(unknown_scores)
    return dict(known_queries=len(known), known_correct=known_correct,
                known_wrong=known_wrong, known_rejected=len(known)-known_correct-known_wrong,
                absent_queries=len(unknown), absent_false_accepts=unknown_false,
                absent_rejected=len(unknown)-unknown_false,
                novelty_ceiling=novelty_ceiling,
                existing_speaker_duplicate_pair_opportunities=duplicate,
                guarded_duplicate_pair_opportunities=guarded_duplicate,
                new_speaker_admission_pair_opportunities=novel,
                guarded_new_speaker_pair_opportunities=guarded_novel)


def evaluate_adaptation(vectors, references, queries, begin, threshold, margin):
    """Chronological high-confidence update, with no ground-truth access in decisions."""
    dimensions = len(next(iter(vectors.values())))
    profiles = {owner:[vectors[references[owner][0]]] for owner in range(begin, begin+10)}
    own_queries = {owner:[key for index, key in queries if index == owner]
                   for owner in profiles}
    def compare(key):
        centers = [(owner, unit_any([sum(sample[i] for sample in samples)/len(samples)
                                  for i in range(dimensions)]))
                   for owner, samples in profiles.items()]
        ordered = sorted(((cosine(vectors[key], center), owner) for owner,center in centers),
                         key=lambda item:(-item[0],item[1]))
        return ordered[0][0], ordered[0][0]-ordered[1][0], ordered[0][1]
    correct = wrong = rejected = updates = wrong_updates = 0
    for turn in range(5):
        for owner in range(begin, begin+10):
            key = own_queries[owner][turn]
            top = compare(key)
            if accepted(top, threshold, margin):
                if top[2] == owner: correct += 1
                else: wrong += 1
                if top[0] >= threshold+margin and top[1] >= 2*margin and len(profiles[top[2]]) < 3:
                    profiles[top[2]].append(vectors[key])
                    updates += 1
                    wrong_updates += top[2] != owner
            else:
                rejected += 1
    absent = [(owner,key) for owner,key in queries if begin+10 <= owner < begin+15]
    false_accepts = sum(accepted(compare(key),threshold,margin) for _,key in absent)
    return dict(known_queries=50, known_correct=correct, known_wrong=wrong,
                known_rejected=rejected, updates=updates, wrong_updates=wrong_updates,
                absent_queries=len(absent), absent_false_accepts=false_accepts,
                update_gate='score>=threshold+margin AND margin>=2*minimum_margin; max3 samples')


def choose_threshold(vectors, references, queries, margin):
    # No fitted threshold may benefit from a held-out query. Ties prefer the
    # stricter threshold; a zero on 25 absent queries is not a population bound.
    candidates = [round(i/1000, 3) for i in range(350, 851, 5)]
    rows = [(t, evaluate(vectors, references, queries, 0, t, margin)) for t in candidates]
    safe = [(t, result) for t, result in rows
            if result['absent_false_accepts'] == 0 and result['known_wrong'] == 0]
    if not safe:
        raise ValueError('No development threshold met zero observed false accepts')
    return max(safe, key=lambda row: (row[1]['known_correct'], row[0]))[0]


def load_recordings(panel, root):
    paths = {}
    for row in panel:
        path = (root / row['audio_file']).resolve()
        if not path.is_relative_to(root.resolve()) or digest(path) != row['audio_sha256']:
            raise ValueError('Recording binding differs')
        paths[row['id']] = path
    return paths


class ModelRunner:
    def __init__(self, frontend, model):
        self.frontend = C.CDLL(str(frontend))
        self.frontend.aiii_uid_fbank.argtypes = [C.c_void_p, C.c_size_t, C.c_int,
            C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t), C.c_void_p, C.c_void_p]
        self.frontend.aiii_uid_fbank.restype = C.c_int
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(str(model), sess_options=options,
                                            providers=['CPUExecutionProvider'])
        if (len(self.session.get_inputs()) != 1 or self.session.get_inputs()[0].name != 'feats' or
                len(self.session.get_outputs()) != 1 or self.session.get_outputs()[0].name != 'embs'):
            raise ValueError('Challenger ONNX input/output names differ from bound frontend')

    def embed(self, pcm):
        samples = len(pcm)//2
        # The frozen public panel follows the native ABI's 30-second bound;
        # live per-turn capture currently uses a stricter 10-second cap. This
        # distinction is stated in the result and prevents a live claim.
        if len(pcm) % 2 or samples < 31920 or samples > 480000:
            raise ValueError('Recording outside native speaker model bound')
        expected = 1+(samples-400)//160
        features = np.empty((1, expected, 80), dtype=np.float32)
        source = C.create_string_buffer(pcm)
        frames = C.c_size_t()
        code = self.frontend.aiii_uid_fbank(source, len(pcm), 16000,
                  features.ctypes.data, features.size, C.byref(frames), None, None)
        if code or frames.value != expected:
            raise ValueError('Native speaker frontend refused bounded recording: '+str(code))
        output = self.session.run(['embs'], {'feats':features})[0]
        if output.shape != (1,256) or not np.isfinite(output).all():
            raise ValueError('Challenger ONNX embedding geometry/values differ')
        values = output[0].astype(np.float64)
        norm = math.sqrt(float(np.dot(values, values)))
        if norm < 1e-12:
            raise ValueError('Invalid speaker embedding norm')
        return (values/norm).tolist()


def infer(paths, library, frontend, model, compare_native):
    vectors = {}
    runner = ModelRunner(frontend, model)
    parity = 0.
    for index, (key, path) in enumerate(paths.items()):
        with wave.open(str(path), 'rb') as source:
            if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16000, 1, 2):
                raise ValueError('16 kHz mono PCM16 required')
            pcm = source.readframes(source.getnframes())
        vectors[key] = runner.embed(pcm)
        if compare_native and index < 3:
            expected = embed(library, model, pcm)
            parity = max(parity, max(abs(a-b) for a,b in zip(vectors[key], expected)))
            if parity > 0.0001:
                raise ValueError('Python ORT differs from shipped native inference')
        if index % 30 == 29:
            print(json.dumps({'completed_recordings': index+1, 'total_recordings': len(paths)}), flush=True)
    return vectors, parity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('panel', 'panel-audio', 'library', 'frontend', 'policy', 'baseline-model', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--challenger-model', type=Path)
    parser.add_argument('--challenger-model-sha256')
    for name in ('panel', 'library', 'frontend', 'policy', 'baseline-model'):
        parser.add_argument('--'+name+'-sha256', required=True)
    args = parser.parse_args()
    if bool(args.challenger_model) != bool(args.challenger_model_sha256):
        raise ValueError('Challenger model and digest must be supplied together')
    names = ['panel', 'library', 'frontend', 'policy', 'baseline-model']
    if args.challenger_model:
        names.append('challenger-model')
    bindings = {name: digest(getattr(args, name.replace('-', '_')))
                for name in names}
    for name, actual in bindings.items():
        if actual != getattr(args, name.replace('-', '_')+'_sha256'):
            raise ValueError('Explicit '+name+' binding differs')
    panel = json.loads(args.panel.read_text())['samples']
    if len(panel) != 180 or len({r['id'] for r in panel}) != 180:
        raise ValueError('Expected 180 unique public-speech recordings')
    references = [[panel[i*6]['id']] for i in range(30)]
    queries = [(i, r['id']) for i in range(30) for r in panel[i*6+1:i*6+6]]
    for i in range(30):
        rows = panel[i*6:i*6+6]
        if len({r['speaker_id'] for r in rows}) != 1 or any(
                r['chapter_id'] == rows[0]['chapter_id'] for r in rows[1:]):
            raise ValueError('Speaker or chapter split differs')
    paths = load_recordings(panel, args.panel_audio)
    policy = json.loads(args.policy.read_text())
    protocol = dict(scope=__doc__, bindings=bindings,
                    source_sha256=digest(Path(__file__)),
                    audio_sha256=[r['audio_sha256'] for r in panel],
                    development_speakers=15, heldout_speakers=15,
                    known_gallery_per_half=10, absent_speakers_per_half=5,
                    queries_per_speaker=5, margin=policy['minimum_margin'],
                    baseline_threshold=policy['threshold'],
                    novelty_ceiling_rule='threshold-minus-half-minimum-margin; strict-less-than',
                    adaptation_rule='chronological score>=threshold+margin; margin>=2*minimum_margin; max3 samples',
                    native_audio_max_seconds=30, live_capture_max_seconds=10)
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out/'protocol.json').write_text(json.dumps(protocol, indent=2)+'\n')
    result = dict(completed=False, protocol_sha256=digest(args.out/'protocol.json'))
    started = time.monotonic()
    try:
        arms = {}
        models = [('baseline', args.baseline_model)]
        if args.challenger_model:
            models.append(('challenger', args.challenger_model))
        for title, model in models:
            vectors, parity = infer(paths, args.library, args.frontend, model, title=='baseline')
            threshold = policy['threshold'] if title == 'baseline' else choose_threshold(
                vectors, references, queries, policy['minimum_margin'])
            arms[title] = dict(threshold=threshold, baseline_native_max_absolute_error=parity,
                               development=evaluate(vectors, references, queries, 0,
                                                    threshold, policy['minimum_margin']),
                               heldout=evaluate(vectors, references, queries, 15,
                                                threshold, policy['minimum_margin']),
                               adaptation_development=evaluate_adaptation(vectors,references,queries,0,
                                                                         threshold,policy['minimum_margin']),
                               adaptation_heldout=evaluate_adaptation(vectors,references,queries,15,
                                                                     threshold,policy['minimum_margin']))
            if any(digest(getattr(args, name.replace('-', '_'))) != sha
                   for name, sha in bindings.items()) or any(
                    digest(paths[r['id']]) != r['audio_sha256'] for r in panel):
                raise ValueError('Bound input changed during execution')
        result.update(completed=True, arms=arms)
    except Exception as error:
        result['error'] = type(error).__name__+': '+str(error)
        raise
    finally:
        result['elapsed_seconds'] = time.monotonic()-started
        (args.out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
