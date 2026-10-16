"""Compare pinned ReDimNet2 on the same held-out public UID panel.

Research only: this is the upstream PyTorch CPU implementation, not an ONNX
port or a deployable native engine. No audio, speaker names, embeddings or
utterance text are emitted. A development threshold is frozen before held-out
evaluation. The released beta model and the live registry are untouched.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import time
import wave

import numpy as np
import torch

from scripts.audit_uid_conditions import digest
from scripts.audit_uid_novelty import (choose_threshold, evaluate,
                                        evaluate_adaptation, load_recordings)


def load_model(repo, checkpoint):
    sys.path.insert(0, str(repo))
    from redimnet2.redimnet2 import ReDimNet2Wrap
    # Do not fall back to pickle execution if the official state file is not
    # compatible with PyTorch's restricted loader.
    saved = torch.load(str(checkpoint), map_location='cpu', weights_only=True)
    if set(saved) != {'model_config', 'state_dict'}:
        raise ValueError('Speaker checkpoint structure differs')
    model = ReDimNet2Wrap(**saved['model_config'])
    model.load_state_dict(saved['state_dict'], strict=True)
    model.eval()
    return model


def embed(model, pcm):
    audio = torch.from_numpy(np.frombuffer(pcm, dtype='<i2').copy()).float()/32768
    with torch.inference_mode():
        output = model(audio.unsqueeze(0))
    if output.ndim != 2 or output.shape[0] != 1:
        raise ValueError('Speaker output geometry differs')
    values = output[0].double().tolist()
    norm = math.sqrt(sum(x*x for x in values))
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError('Speaker output invalid')
    return [x/norm for x in values]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('panel', 'panel-audio', 'checkpoint', 'repo', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    for name in ('panel', 'checkpoint'):
        parser.add_argument('--'+name+'-sha256', required=True)
    parser.add_argument('--repo-commit', required=True)
    parser.add_argument('--margin', type=float, required=True)
    args = parser.parse_args()
    if digest(args.panel) != args.panel_sha256 or digest(args.checkpoint) != args.checkpoint_sha256:
        raise ValueError('Bound panel or checkpoint differs')
    import subprocess
    actual = subprocess.check_output(['git','-C',str(args.repo),'rev-parse','HEAD'],text=True).strip()
    if actual != args.repo_commit:
        raise ValueError('Upstream source commit differs')
    panel = json.loads(args.panel.read_text())['samples']
    if len(panel) != 180 or len({r['id'] for r in panel}) != 180:
        raise ValueError('Public panel differs')
    references = [[panel[i*6]['id']] for i in range(30)]
    queries = [(i,r['id']) for i in range(30) for r in panel[i*6+1:i*6+6]]
    for i in range(30):
        rows = panel[i*6:i*6+6]
        if len({r['speaker_id'] for r in rows}) != 1 or any(
                r['chapter_id'] == rows[0]['chapter_id'] for r in rows[1:]):
            raise ValueError('Speaker or chapter partition differs')
    paths = load_recordings(panel,args.panel_audio)
    source = Path(__file__)
    protocol = dict(scope=__doc__, panel_sha256=args.panel_sha256,
        checkpoint_sha256=args.checkpoint_sha256, repo_commit=actual,
        source_sha256=digest(source), audio_sha256=[r['audio_sha256'] for r in panel],
        development_speakers=15, heldout_speakers=15, known_gallery_per_half=10,
        absent_speakers_per_half=5, queries_per_speaker=5, minimum_margin=args.margin,
        device='cpu', weights_only=True)
    args.out.mkdir(parents=True,exist_ok=False)
    (args.out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    result = dict(completed=False, protocol_sha256=digest(args.out/'protocol.json'))
    started=time.monotonic()
    try:
        torch.set_num_threads(2)
        model=load_model(args.repo,args.checkpoint)
        vectors={}
        for i,(key,path) in enumerate(paths.items()):
            with wave.open(str(path),'rb') as audio:
                if (audio.getframerate(),audio.getnchannels(),audio.getsampwidth()) != (16000,1,2):
                    raise ValueError('16 kHz mono PCM16 required')
                pcm=audio.readframes(audio.getnframes())
            vectors[key]=embed(model,pcm)
            if i%30==29:
                print(json.dumps({'completed_recordings':i+1,'total_recordings':180}),flush=True)
        threshold=choose_threshold(vectors,references,queries,args.margin)
        result.update(completed=True,embedding_dimension=len(next(iter(vectors.values()))),
            threshold=threshold,
            development=evaluate(vectors,references,queries,0,threshold,args.margin),
            heldout=evaluate(vectors,references,queries,15,threshold,args.margin),
            adaptation_development=evaluate_adaptation(vectors,references,queries,0,threshold,args.margin),
            adaptation_heldout=evaluate_adaptation(vectors,references,queries,15,threshold,args.margin))
        if digest(args.panel)!=args.panel_sha256 or digest(args.checkpoint)!=args.checkpoint_sha256 or any(
            digest(paths[r['id']])!=r['audio_sha256'] for r in panel):
            raise ValueError('Bound input changed during execution')
    except Exception as error:
        result['error']=type(error).__name__+': '+str(error)
        raise
    finally:
        result['elapsed_seconds']=time.monotonic()-started
        (args.out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result),flush=True)


if __name__ == '__main__':
    main()
