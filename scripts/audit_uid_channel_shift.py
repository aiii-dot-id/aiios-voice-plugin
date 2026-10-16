"""Frozen held-out speaker-profile challenge with deterministic channel shifts.

First two clean recordings of each known speaker are references. The same
held-out queries are tested clean, through a telephone-like bandpass, and
with 20 dB Gaussian noise. This deliberately simple stress test is NOT a
replacement for physical microphone, overlap or SVeritas qualification.
No audio, speaker vectors or personal identity data is written to results.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import wave

import numpy as np
from scipy.signal import butter, sosfilt

from scripts.audit_uid_conditions import digest
from scripts.audit_uid_novelty import ModelRunner, load_recordings
from scripts.audit_uid_profile_pooling import evaluate


def changed(pcm, name, key):
    if name == 'clean':
        return pcm
    values = np.frombuffer(pcm,dtype='<i2').astype(np.float64)/32768.0
    if name == 'telephone':
        values = sosfilt(butter(4,[300,3400],btype='bandpass',fs=16000,
                                output='sos'),values)*0.8
    elif name == 'noise20':
        seed = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],'big')
        rng = np.random.default_rng(seed)
        noise = rng.standard_normal(len(values))
        speech_rms = max(1e-7,float(np.sqrt(np.mean(values*values))))
        noise *= speech_rms/(10.0*np.sqrt(np.mean(noise*noise)))
        values = values + noise
    else:
        raise ValueError('Unknown bounded condition')
    return (np.clip(values,-1,1)*32767).astype('<i2').tobytes()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('panel','panel-audio','frontend','model','out'):
        p.add_argument('--'+name,type=Path,required=True)
    for name in ('panel','frontend','model'):
        p.add_argument('--'+name+'-sha256',required=True)
    p.add_argument('--threshold',type=float,required=True)
    p.add_argument('--margin',type=float,required=True)
    args=p.parse_args()
    bindings={name:digest(getattr(args,name)) for name in ('panel','frontend','model')}
    if any(value!=getattr(args,name+'_sha256') for name,value in bindings.items()):
        raise ValueError('Bound source differs')
    panel=json.loads(args.panel.read_text())['samples']
    if len(panel)!=180 or len({r['id'] for r in panel})!=180:
        raise ValueError('Frozen public panel differs')
    paths=load_recordings(panel,args.panel_audio)
    args.out.mkdir(parents=True,exist_ok=False)
    protocol=dict(scope=__doc__,bindings=bindings,source_sha256=digest(Path(__file__)),
                  conditions=['clean','telephone','noise20'],
                  panel_audio_sha256=[r['audio_sha256'] for r in panel],
                  only_heldout_speakers=True,max_input_seconds=10,
                  unchanged_policy_threshold=args.threshold,margin=args.margin)
    (args.out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    result=dict(completed=False,protocol_sha256=digest(args.out/'protocol.json'))
    started=time.monotonic()
    try:
        runner=ModelRunner(args.frontend,args.model)
        clean={}
        selected=panel[15*6:]
        for index,row in enumerate(selected):
            with wave.open(str(paths[row['id']]),'rb') as source:
                if (source.getframerate(),source.getnchannels(),source.getsampwidth())!=(16000,1,2):
                    raise ValueError('Public panel format differs')
                clean[row['id']]=source.readframes(min(source.getnframes(),160000))
        references={r['id'] for owner in range(15,25) for r in panel[owner*6:owner*6+2]}
        vectors={key:runner.embed(pcm) for key,pcm in clean.items()}
        rows={}
        for condition in protocol['conditions']:
            arm=dict(vectors)
            if condition!='clean':
                for index,(key,pcm) in enumerate(clean.items()):
                    if key in references:continue
                    arm[key]=runner.embed(changed(pcm,condition,key))
                    if index%30==29:
                        print(json.dumps({'condition':condition,'reached_recording':index+1}),flush=True)
            rows[condition]={str(samples):evaluate(arm,panel,15,args.threshold,args.margin,samples,'centroid')
                             for samples in (1,2)}
        if any(digest(getattr(args,name))!=value for name,value in bindings.items()) or any(
                digest(paths[r['id']])!=r['audio_sha256'] for r in panel):
            raise ValueError('Bound source changed during run')
        result.update(completed=True,conditions=rows)
    except Exception as error:
        result['error']=type(error).__name__+': '+str(error)
        raise
    finally:
        result['elapsed_seconds']=time.monotonic()-started
        (args.out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result),flush=True)


if __name__=='__main__':
    main()
