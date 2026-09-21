"""Qualify raw PCM through native frontend, diarization and conditioned ASR."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import time
import wave

from .prove_native_multitalker import verify_graphs
from .run_speaker_aware_reference import digest, write_json
from .score_speaker_aware import evaluate


def expected_tokens(path):
    """Read references outside inference; they never enter the native child."""
    expected={}
    with path.open('rb') as f:
        if f.read(8)!=b'AIIMTC01':raise ValueError('capture format')
        while header:=f.read(20):
            epoch,frames,valid,drop,final=struct.unpack('<IIIII',header)
            if not 0<valid<=frames<=1024 or drop>2 or final>1:raise ValueError('capture extent')
            if len(f.read(frames*128*4))!=frames*128*4:raise ValueError('truncated features')
            row=[]
            for _ in range(4):
                count=struct.unpack('<I',f.read(4))[0]
                if count>65536:raise ValueError('reference bound')
                row.append(list(struct.unpack('<'+'I'*count,f.read(count*4))))
            expected[epoch]=row
    return expected


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('graphs','frontend','trace','panel','probe','ort-library','out'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    result=dict(passed=False,installed=False,persistent_uid_qualified=False,
                scope='native raw PCM speaker-separated recognition; not installed product',process_retired=False)
    try:
        verify_graphs(a.graphs)
        frontend=json.loads((a.frontend/'result.json').read_text())
        if digest(a.frontend/'mel.f32')!=frontend['files']['mel.f32']:raise ValueError('frontend binding')
        trace=json.loads((a.trace/'result.json').read_text())
        if not trace['passed'] or digest(a.trace/'capture.trace')!=trace['capture_trace_sha256']:
            raise ValueError('reference capture binding')
        panel=json.loads(a.panel.read_text());files=[]
        for i,case in enumerate(panel['cases']):
            source=a.panel.parent/case['audio_file']
            if digest(source)!=case['audio_sha256']:raise ValueError('recording changed')
            with wave.open(str(source),'rb') as f:
                if (f.getnchannels(),f.getsampwidth(),f.getframerate(),f.getnframes())!=(1,2,16000,case['samples']):
                    raise ValueError('recording format')
                pcm=f.readframes(f.getnframes())
            # No reference words or masks are available to the executable.
            values=struct.unpack('<'+'h'*(len(pcm)//2),pcm)
            target=a.out/(str(i)+'.f32')
            target.write_bytes(struct.pack('<'+'f'*len(values),*(x/32768. for x in values)))
            files.append(target)
        result.update(bindings={str(path):digest(path) for path in
            (a.probe,a.ort_library,a.graphs/'result.json',a.frontend/'result.json',a.trace/'result.json',a.panel)},
            sources={str(path.relative_to(Path(__file__).resolve().parents[1])):digest(path)
                for path in (Path(__file__).resolve().parents[1]/'runtime/native_multitalker').iterdir() if path.is_file()})
        with (a.out/'stdout.jsonl').open('x') as stdout,(a.out/'stderr.log').open('x') as stderr:
            run=subprocess.run([str(a.probe),str(a.graphs),str(a.frontend/'mel.f32'),*map(str,files)],
                stdout=stdout,stderr=stderr,timeout=900)
        result.update(exit_code=run.returncode,process_retired=True)
        if run.returncode:raise ValueError('native raw audio execution failed; see stderr.log')
        rows=[json.loads(x) for x in (a.out/'stdout.jsonl').read_text().splitlines()]
        if len(rows)!=len(panel['cases']):raise ValueError('native case census')
        references=expected_tokens(a.trace/'capture.trace')
        vocabulary=json.loads((a.graphs/'tokens.json').read_text())
        hypotheses={};parity=[]
        for i,(case,row) in enumerate(zip(panel['cases'],rows)):
            if row['samples']!=case['samples'] or len(row['tracks'])!=4:raise ValueError('output geometry')
            parity.append(row['tracks']==references[i+1])
            hypotheses[case['id']]=[dict(speaker=str(track),start_time=0,end_time=case['samples']/16000,
                words=''.join(vocabulary[token] for token in tokens).replace('▁',' ').strip())
                for track,tokens in enumerate(row['tracks']) if tokens]
        write_json(a.out/'hypotheses.json',hypotheses)
        result.update(score=evaluate(panel,hypotheses),exact_token_parity=parity,
            tokens=sum(len(t) for row in rows for t in row['tracks']),
            peak_retained_samples=max(r['peak_retained_samples'] for r in rows))
        if any(digest(path)!=sha for path,sha in result['bindings'].items()):raise ValueError('binding changed during run')
        verify_graphs(a.graphs)
        result['passed']=all(parity) and result['score']['passed']
    except Exception as error:result['error']=repr(error)
    finally:
        result['elapsed_seconds']=time.monotonic()-started
        write_json(a.out/'result.json',result)
        print(json.dumps({k:result.get(k) for k in ('passed','error','tokens','exact_token_parity','elapsed_seconds')}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__=='__main__':main()
