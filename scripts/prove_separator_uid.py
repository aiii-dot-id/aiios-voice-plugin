"""Offline separator-to-UID gate with exact inputs and unchanged decision policy.

No live session, enrollment, registry, microphone, deployment or authorization
is opened. Uses the panel's existing speaker-disjoint calibration/evaluation
split and original three-reference galleries. Labels select the corpus split
and grade output permutations; they never enter separator inference. Recorded
outputs and embeddings are not saved. This is not transcript qualification.
"""
import argparse
import ctypes as C
import itertools
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np

from scripts.export_mossformer2_separator import CHECKPOINT_SHA256, UPSTREAM_REVISION, digest, write
from scripts.separator_audio import normalized_sources, pcm16


def unit(x):
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    norm = np.linalg.norm(x)
    if not np.isfinite(x).all() or norm < 1e-10:
        raise ValueError('invalid speaker embedding')
    return x/norm


def identify(vector, gallery, threshold, margin):
    scores = sorted(((float(vector@ref), name) for name, ref in gallery.items()), reverse=True)
    if len(scores) < 2:
        raise ValueError('comparison needs competing profiles')
    score, name = scores[0]
    gap = score-scores[1][0]
    accepted = score >= threshold and gap >= margin and gap > 0
    return {'speaker': name if accepted else None, 'score': score, 'margin': gap}


def metrics(outputs):
    return {'known': sum(o['known'] for o in outputs),
        'correct': sum(o['known'] and o['decision']['speaker'] == o['truth'] for o in outputs),
        'wrong': sum(o['decision']['speaker'] is not None and o['decision']['speaker'] != o['truth'] for o in outputs),
        'unknown': sum(not o['known'] for o in outputs)}


def si_sdr(estimate, source):
    estimate = estimate.astype(np.float64)-estimate.mean()
    source = source.astype(np.float64)-source.mean()
    projection = source*(estimate@source)/max(float(source@source), 1e-12)
    return float(10*np.log10((projection@projection+1e-12)/(np.sum((estimate-projection)**2)+1e-12)))


def native_uid(frontend, graph):
    import onnxruntime as ort
    lib = C.CDLL(str(frontend.resolve()))
    lib.aii_ecapa_fbank.argtypes = [C.c_void_p, C.c_size_t, C.c_int, C.c_void_p,
        C.c_size_t, C.POINTER(C.c_size_t), C.c_void_p, C.c_void_p]
    lib.aii_ecapa_fbank.restype = C.c_int
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(graph), sess_options=options, providers=['CPUExecutionProvider'])

    def embed(pcm):
        if not 32000 <= len(pcm) <= 160000:
            raise ValueError('comparison PCM outside qualified duration')
        raw = pcm16(pcm)
        feats = np.empty((1, 1+len(pcm)//160, 80), dtype=np.float32)
        frames = C.c_size_t()
        rc = lib.aii_ecapa_fbank(raw, len(raw), 16000, feats.ctypes.data, feats.size,
                                  C.byref(frames), None, None)
        if rc or frames.value != feats.shape[1]:
            raise ValueError('native frontend refused')
        return unit(session.run(['embs'], {'feats': feats})[0])
    return embed


def mossformer(source, checkpoint, device):
    import torch
    if digest(checkpoint) != CHECKPOINT_SHA256:
        raise ValueError('separator checkpoint differs')
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True)
    if revision != UPSTREAM_REVISION or dirty:
        raise ValueError('exact clean separator source required')
    if device == 'mps' and not torch.backends.mps.is_available():
        raise ValueError('MPS unavailable')
    if device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA unavailable')
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    sys.path.insert(0, str(source.resolve()/'clearvoice/clearvoice'))
    from models.mossformer2_ss.mossformer2 import MossFormer2_SS_16K
    model = MossFormer2_SS_16K(SimpleNamespace(encoder_embedding_dim=512,
        mossformer_sequence_dim=512, num_mossformer_layer=24, encoder_kernel_size=16, num_spks=2)).eval()
    model.model.load_state_dict(torch.load(checkpoint, weights_only=True, map_location='cpu')['model'], strict=True)
    model.to(device)

    def run(x):
        with torch.inference_mode():
            return [y.reshape(-1).cpu().numpy() for y in model(torch.from_numpy(x).unsqueeze(0).to(device))]
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'checkpoint', 'panel', 'frontend', 'uid', 'policy', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--device', choices=('cpu', 'mps', 'cuda'), required=True)
    args = parser.parse_args()
    args.out.mkdir(mode=0o700, parents=True, exist_ok=False)
    started = time.monotonic()
    result = {'completed': False, 'passed': False, 'scope': 'offline waveform UID component only'}
    try:
        import os
        if os.environ.get('PYTORCH_ENABLE_MPS_FALLBACK') == '1':
            raise ValueError('implicit fallback is forbidden')
        manifest = json.loads((args.panel/'manifest.json').read_text())
        policy = json.loads(args.policy.read_text())
        threshold, margin = policy['threshold'], policy['minimum_margin']
        if not -1 <= threshold <= 1 or not 0 <= margin <= 2:
            raise ValueError('invalid decision policy')
        rows = manifest['samples']
        cohorts = manifest['protocol']
        groups = [set(cohorts[s+'_'+role]) for s in ('calibration', 'evaluation') for role in ('known', 'unknown')]
        if any(a & b for a, b in itertools.combinations(groups, 2)):
            raise ValueError('speaker cohorts overlap')
        bindings = {name+'_sha256': digest(getattr(args, name)) for name in ('checkpoint', 'frontend', 'uid', 'policy')}
        write(args.out/'protocol.json', {**bindings, 'script_sha256': digest(__file__),
            'audio_boundary_sha256': digest(Path(__file__).with_name('separator_audio.py')),
            'panel_sha256': digest(args.panel/'manifest.json'), 'source_revision': UPSTREAM_REVISION,
            'device': args.device, 'threshold': threshold, 'margin': margin,
            'gallery': 'three complete original enrollment recordings, mean then unit',
            'query': 'first clean non-enrollment row at least 4.5 seconds, in manifest order',
            'pairs': 'adjacent known; each known with cycling unknown; adjacent unknown',
            'gains': [[1, 1], [1, .25], [.25, 1]], 'seconds': 4.5,
            'requires': 'all clean known and separated known correct, no wrong or unknown accepts',
            'limitation': 'previously exercised public corpus, correlated mixtures, not broad UID accuracy or word attribution'})

        def audio(row, samples=None):
            path = args.panel/row['file']
            if digest(path) != row['sha256']:
                raise ValueError('panel recording changed')
            raw = path.read_bytes()
            if len(raw) % 2:
                raise ValueError('partial PCM sample')
            x = np.frombuffer(raw, dtype='<i2').astype(np.float32)/32768
            if samples is not None and len(x) < samples:
                raise ValueError('short frozen sample')
            return x if samples is None else x[:samples]

        embed = native_uid(args.frontend, args.uid)
        separate = mossformer(args.source, args.checkpoint, args.device)
        summaries = []
        for split in ('calibration', 'evaluation'):
            known, unknown = cohorts[split+'_known'], cohorts[split+'_unknown']
            gallery = {}
            enrolled_hashes = set()
            for s in known:
                refs = [r for r in rows if r['speaker_id'] == s and r['role'] == 'enrollment']
                if len(refs) != 3:
                    raise ValueError('original gallery must have three references')
                enrolled_hashes.update(r['sha256'] for r in refs)
                gallery[s] = unit(sum(embed(audio(r)) for r in refs))
            queries = {}
            for s in known+unknown:
                row = next(r for r in rows if r['speaker_id'] == s and r['condition'] == 'clean' and
                    r['role'] != 'enrollment' and (args.panel/r['file']).stat().st_size >= 144000)
                if row['sha256'] in enrolled_hashes:
                    raise ValueError('reference/query audio overlap')
                queries[s] = audio(row, 72000)
            decide = lambda x: identify(embed(x), gallery, threshold, margin)
            controls = [{'truth': s, 'known': s in known, 'decision': decide(x)} for s, x in queries.items()]
            pairs = list(zip(known[::2], known[1::2]))+[(s, unknown[i % len(unknown)]) for i, s in enumerate(known)]+list(zip(unknown[::2], unknown[1::2]))
            trials = []
            for left, right in pairs:
                for gl, gr in ((1, 1), (1, .25), (.25, 1)):
                    sources = [queries[left]*gl, queries[right]*gr]
                    gain = .8/max(1., float(np.abs(sum(sources)).max()))
                    sources = [x*gain for x in sources]
                    mixture = sum(sources)
                    begin = time.monotonic()
                    outputs = normalized_sources(mixture, separate(mixture))
                    seconds = time.monotonic()-begin
                    # Make decisions BEFORE consulting ground truth for grading.
                    decisions = [decide(x) for x in outputs]
                    order = max(itertools.permutations(range(2)), key=lambda p:
                        sum(si_sdr(outputs[p[i]], sources[i]) for i in range(2)))
                    trial = {'speakers': [left, right], 'gains': [gl, gr], 'seconds': seconds,
                        'outputs': [{'truth': s, 'known': s in known, 'decision': decisions[order[i]],
                            'sisdr_gain_db': si_sdr(outputs[order[i]], sources[i])-si_sdr(mixture, sources[i])}
                            for i, s in enumerate((left, right))]}
                    trials.append(trial)
                    write(args.out/f'{split}-trial-{len(trials):03d}.json', trial)
            measured = metrics([o for t in trials for o in t['outputs']])
            clean = metrics(controls)
            summary = {'split': split, 'clean': clean, 'separated': measured}
            summaries.append(summary)
            write(args.out/(split+'.json'), {'controls': controls, 'metrics': summary})
            print(json.dumps(summary), flush=True)
        result.update(completed=True, summaries=summaries,
            passed=all(m['correct'] == m['known'] and not m['wrong'] for s in summaries for m in (s['clean'], s['separated'])))
        return 0 if result['passed'] else 1
    except BaseException as e:
        result['failure'] = str(e)
        raise
    finally:
        result['elapsed_seconds'] = time.monotonic()-started
        write(args.out/'result.json', result)


if __name__ == '__main__':
    raise SystemExit(main())
