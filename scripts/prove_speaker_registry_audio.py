"""Real activity-selected PCM -> native UID -> restartable anonymous registry.

Development-only evidence. References score results; they never select PCM,
drive the matcher, or name a profile. This is not installed qualification.
"""
import argparse
import base64
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time
import uuid
import wave

from .run_speaker_aware_reference import digest, write_json
from .native_checkpoint_binding import verify_checkpoint


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('activity','panel','checkpoint','probe','out'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    os.chmod(a.out,0o700)
    started=time.monotonic();lib=None;owner=None
    result=dict(passed=False,installed=False,scope=__doc__,cases=[],processes_retired=False)
    try:
        evidence=json.loads((a.activity/'result.json').read_text())
        if not evidence['passed'] or not all(evidence['exact_token_parity']):raise ValueError('native activity source did not pass')
        panel=json.loads(a.panel.read_text())
        rows=[json.loads(x) for x in (a.activity/'stdout.jsonl').read_text().splitlines()]
        if len(rows)!=len(panel['cases']):raise ValueError('activity case census')
        required=['solo_a','solo_b','alternating','overlap_equal','overlap_b_quiet','overlap_a_quiet','overlap_cold']
        if [c['id'] for c in panel['cases']]!=required:raise ValueError('frozen acoustic panel case order differs')
        root=a.checkpoint
        _,_,_,checkpoint_bindings=verify_checkpoint(root.resolve())
        model=root/'data/uid/model.onnx';library=root/'runtime/lib/libaii_native_uid.dylib'
        policy=(root/'runtime/resources/uid-policy.json').read_text();pd=json.loads(policy)
        if pd['embedding_binding']!='2575d15495d0e1cf17a2a12b0163dec0639db6218b3fe810e70369002d3898da':raise ValueError('UID binding differs')
        if digest(model)!='5b734353b4b410e222bbd124dd095537642237ad895727d18a3b9fee330262a8' or model.stat().st_size!=79158228:raise ValueError('UID model bytes differ')
        result['bindings']={str(x):digest(x) for x in (model,library,root/'runtime/resources/uid-policy.json',a.probe,a.panel,a.activity/'result.json',a.activity/'stdout.jsonl')}
        result['bindings'].update(checkpoint_bindings)
        result['bindings'].update(evidence['bindings'])
        result['bindings'][str(Path(__file__).resolve())]=digest(__file__)
        for path,sha in result['bindings'].items():
            if digest(path)!=sha:raise ValueError('native evidence binding changed before UID proof')
        os.environ['ORT_DISABLE_TELEMETRY']='1'
        lib=C.CDLL(str(library.resolve()))
        lib.aii_uid_create.argtypes=[C.c_void_p,C.c_size_t,C.c_char_p,C.c_void_p,C.c_size_t];lib.aii_uid_create.restype=C.c_void_p
        lib.aii_uid_embed.argtypes=[C.c_void_p,C.c_uint64,C.c_void_p,C.c_size_t,C.c_int,C.POINTER(C.c_double),C.c_size_t,C.c_void_p,C.c_size_t];lib.aii_uid_embed.restype=C.c_int
        lib.aii_uid_destroy.argtypes=[C.c_void_p]
        model_buffer=C.create_string_buffer(model.read_bytes());error=C.create_string_buffer(1024)
        owner=lib.aii_uid_create(model_buffer,len(model_buffer)-1,b'cpu',error,len(error))
        if not owner:raise ValueError('UID model load: '+error.value.decode())
        document=None;revision=0;counter=0;baseline={};coverage=[]
        for case,row in zip(panel['cases'],rows):
            wav=a.panel.parent/case['audio_file']
            if digest(wav)!=case['audio_sha256']:raise ValueError('recorded audio changed')
            with wave.open(str(wav),'rb') as f:
                if (f.getframerate(),f.getnchannels(),f.getsampwidth())!=(16000,1,2):raise ValueError('PCM format')
                pcm=f.readframes(f.getnframes())
            mapping={int(s['hypothesis_track']):s['reference_speaker'] for s in evidence['score']['cases'][case['id']]['speakers']}
            if len(row['tracks'])!=4 or {t for t,tokens in enumerate(row['tracks']) if tokens}!=set(mapping):raise ValueError('speaker text/track census differs')
            selected={s['track']:s for s in row['evidence_spans']}
            if len(selected)!=len(row['evidence_spans']) or not set(selected)<=set(mapping):raise ValueError('duplicate or foreign evidence track')
            decisions=[]
            for track,tokens in enumerate(row['tracks']):
                if not tokens:continue
                span=selected.get(track);sample=None;purity=True
                if span:
                    start,end=span['start'],span['end']
                    if not 0<=start<end<=len(pcm)//2 or not 31920<=end-start<=160000:raise ValueError('selected evidence extent')
                    raw=pcm[2*start:2*end];counter+=1
                    data=C.create_string_buffer(raw);vector=(C.c_double*256)()
                    code=lib.aii_uid_embed(owner,counter,data,len(raw),16000,vector,256,error,len(error))
                    if code:raise ValueError('UID embedding: '+error.value.decode())
                    sample=dict(audio_sha256=hashlib.sha256(raw).hexdigest(),embedding_f64le_b64=base64.b64encode(struct.pack('<256d',*vector)).decode())
                    # Conservative reference-envelope exclusion, not a claimed
                    # measured zero-energy detector. All scoring is post hoc.
                    for ref in case['reference']:
                        if ref['speaker']!=mapping[track] and max(start,round(ref['start_time']*16000))<min(end,round(ref['end_time']*16000)):
                            purity=False
                request=dict(operation='observe',policy=policy,document=document,expected_revision=revision,uuid=str(uuid.uuid4()),sample=sample)
                run=subprocess.run([str(a.probe)],input=json.dumps(request),text=True,capture_output=True,timeout=20)
                if run.returncode:raise ValueError('native registry refused: '+run.stderr.strip())
                reply=json.loads(run.stdout);document=reply['document'];revision=reply['revision']
                # Each call is a fresh process: returning matches cannot depend
                # on an in-memory map from reference labels or track numbers.
                speaker=mapping[track]
                if case['id'] in ('solo_a','solo_b'):
                    baseline[speaker]=reply['uuid'];correct=reply['continuity']=='new_profile'
                elif sample:
                    correct=reply['continuity']=='matched' and reply['uuid']==baseline.get(speaker)
                else:
                    correct=reply['continuity']=='provisional' and reply['uuid'] not in baseline.values()
                decisions.append(dict(track=track,speaker_uuid=reply['uuid'],continuity=reply['continuity'],correct=correct,
                    reference_envelope_excludes_other_speakers=purity,evidence_span=span))
            if not decisions:raise ValueError('empty separation is not success')
            coverage.append(all(d['continuity']!='provisional' for d in decisions))
            result['cases'].append(dict(id=case['id'],decisions=decisions,passed=all(d['correct'] and d['reference_envelope_excludes_other_speakers'] for d in decisions)))
        # Labeling after every inference owner has returned, in another process.
        if len(baseline)!=2 or len(set(baseline.values()))!=2:raise ValueError('two distinct anonymous profiles required')
        first=next(iter(baseline.values()))
        run=subprocess.run([str(a.probe)],input=json.dumps(dict(operation='associate',policy=policy,document=document,
            expected_revision=revision,uuid=first,label='Operator-selected label')),text=True,capture_output=True,timeout=20)
        if run.returncode:raise ValueError('post-session association refused')
        named=json.loads(run.stdout)
        result.update(association_after_process_restart=named['continuity']=='associated',complete_profile_coverage=all(coverage),
                      matched_cases=sum(coverage),total_cases=len(coverage),processes_retired=True)
        if any(digest(path)!=sha for path,sha in result['bindings'].items()):raise ValueError('inputs changed during proof')
        result['passed']=all(c['passed'] for c in result['cases']) and result['association_after_process_restart']
    except Exception as e:result['error']=str(e)
    finally:
        if owner:lib.aii_uid_destroy(owner)
        result['elapsed_seconds']=time.monotonic()-started
        write_json(a.out/'result.json',result)
        print(json.dumps({k:result.get(k) for k in ('passed','error','matched_cases','total_cases','complete_profile_coverage','elapsed_seconds')}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__=='__main__':main()
