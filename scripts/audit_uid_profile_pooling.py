"""Compare one- and two-recording speaker profiles on a frozen public panel.

Development selects thresholds without reading held-out decisions. The second
reference is a different utterance/chapter; both arms answer the same four
remaining queries per known speaker. This is a component audit, not release
qualification. No audio, speaker vectors or private identity data is written.
"""
import argparse
from itertools import combinations
import json
from pathlib import Path
import time
import wave

from scripts.audit_uid_conditions import digest
from scripts.audit_uid_novelty import ModelRunner, accepted, cosine, load_recordings, score, unit_any


def evaluate(vectors, panel, begin, threshold, margin, samples, rule):
    gallery = {}
    for owner in range(begin, begin+10):
        rows = panel[owner*6:owner*6+samples]
        gallery[owner] = [vectors[r['id']] for r in rows]
    if rule == 'centroid':
        gallery = {owner:[unit_any([sum(row[i] for row in rows)/len(rows)
                                    for i in range(len(rows[0]))])]
                   for owner, rows in gallery.items()}
    result = dict(known_queries=0, known_correct=0, known_wrong=0,
                  known_rejected=0, absent_queries=0, absent_false_accepts=0)
    duplicate = 0
    for owner in range(begin, begin+15):
        rows = panel[owner*6+2:owner*6+6] if owner < begin+10 else panel[owner*6+1:owner*6+6]
        for row in rows:
            scores = []
            for candidate, refs in gallery.items():
                pair = [cosine(vectors[row['id']], ref) for ref in refs]
                value = (max(pair) if rule == 'max' else min(pair)) if rule != 'centroid' else pair[0]
                scores.append((value,candidate))
            scores.sort(key=lambda item:(-item[0],item[1]))
            top = (scores[0][0],scores[0][0]-scores[1][0],scores[0][1])
            ok = accepted(top,threshold,margin)
            if owner < begin+10:
                result['known_queries'] += 1
                result['known_correct' if ok and top[2] == owner else
                       'known_wrong' if ok else 'known_rejected'] += 1
            else:
                result['absent_queries'] += 1
                result['absent_false_accepts'] += ok
    return result


def choose(vectors,panel,begin,samples,rule,margin):
    candidates = []
    for index in range(350,851,5):
        threshold=index/1000
        row=evaluate(vectors,panel,begin,threshold,margin,samples,rule)
        if row['known_wrong']==0 and row['absent_false_accepts']==0:
            candidates.append((row['known_correct'],threshold))
    if not candidates:
        raise ValueError('No development threshold met zero observed false accepts')
    return max(candidates)[1]


def admission_opportunities(vectors,panel,begin,threshold,margin):
    """Count labelled same/different-speaker pairs, not sequential registry writes."""
    gallery=[(owner,panel[owner*6]['id']) for owner in range(begin,begin+10)]
    unknown=[(owner,r['id']) for owner in range(begin+10,begin+15)
             for r in panel[owner*6+1:owner*6+6]]
    eligible=[(owner,key) for owner,key in unknown
              if not accepted(score(key,gallery,vectors),threshold,margin)]
    counts=dict(same_speaker_pairs=0,same_admitted_before_center_guard=0,
                same_admitted_after_center_guard=0,
                different_speaker_pairs=0,different_admitted_after_center_guard=0)
    for (owner_a,key_a),(owner_b,key_b) in combinations(eligible,2):
        same=owner_a==owner_b
        counts['same_speaker_pairs' if same else 'different_speaker_pairs']+=1
        if cosine(vectors[key_a],vectors[key_b])<threshold:
            continue
        if same:
            counts['same_admitted_before_center_guard']+=1
        center=unit_any([x+y for x,y in zip(vectors[key_a],vectors[key_b])])
        scored=sorted(((cosine(center,vectors[ref]),owner) for owner,ref in gallery),
                      key=lambda item:(-item[0],item[1]))
        # The runtime refuses new profiles on an existing known OR ambiguous
        # decision; either has an above-threshold winner.
        if scored[0][0]>=threshold:
            continue
        counts['same_admitted_after_center_guard' if same else
               'different_admitted_after_center_guard']+=1
    return counts


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('panel','panel-audio','frontend','model','out'):
        p.add_argument('--'+name,type=Path,required=True)
    for name in ('panel','frontend','model'):
        p.add_argument('--'+name+'-sha256',required=True)
    p.add_argument('--baseline-threshold',type=float,required=True)
    p.add_argument('--margin',type=float,required=True)
    p.add_argument('--max-seconds',type=int,choices=(10,30),default=30)
    args=p.parse_args()
    bindings={name:digest(getattr(args,name)) for name in ('panel','frontend','model')}
    for name,value in bindings.items():
        if value != getattr(args,name+'_sha256'):
            raise ValueError('Bound '+name+' changed')
    panel=json.loads(args.panel.read_text())['samples']
    if len(panel)!=180 or len({r['id'] for r in panel})!=180:
        raise ValueError('Frozen panel differs')
    for i in range(30):
        rows=panel[i*6:i*6+6]
        if len({r['speaker_id'] for r in rows})!=1 or rows[0]['chapter_id']==rows[1]['chapter_id']:
            raise ValueError('Panel speaker/chapter split differs')
    paths=load_recordings(panel,args.panel_audio)
    args.out.mkdir(parents=True,exist_ok=False)
    protocol=dict(scope=__doc__,bindings=bindings,source_sha256=digest(Path(__file__)),
                  panel_audio_sha256=[r['audio_sha256'] for r in panel],
                  first_15_speakers='development',last_15_speakers='heldout',
                  second_reference='different_chapter',
                  same_queries_per_arm=True,margin=args.margin,
                  max_input_seconds=args.max_seconds)
    (args.out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    result=dict(completed=False,protocol_sha256=digest(args.out/'protocol.json'))
    started=time.monotonic()
    try:
        runner=ModelRunner(args.frontend,args.model)
        vectors={}
        for index,(key,path) in enumerate(paths.items()):
            with wave.open(str(path),'rb') as source:
                if (source.getframerate(),source.getnchannels(),source.getsampwidth())!=(16000,1,2):
                    raise ValueError('Panel audio format differs')
                vectors[key]=runner.embed(source.readframes(min(source.getnframes(),
                                                          16000*args.max_seconds)))
            if index%30==29:
                print(json.dumps({'completed_recordings':index+1}),flush=True)
        arms={}
        for samples,rule in ((1,'centroid'),(2,'centroid'),(2,'max'),(2,'min')):
            name=f'{samples}_{rule}'
            threshold=args.baseline_threshold if samples==1 else choose(
                vectors,panel,0,samples,rule,args.margin)
            arms[name]=dict(threshold=threshold,
                development=evaluate(vectors,panel,0,threshold,args.margin,samples,rule),
                heldout=evaluate(vectors,panel,15,threshold,args.margin,samples,rule),
                fixed_policy_threshold=args.baseline_threshold,
                fixed_policy_development=evaluate(vectors,panel,0,args.baseline_threshold,args.margin,samples,rule),
                fixed_policy_heldout=evaluate(vectors,panel,15,args.baseline_threshold,args.margin,samples,rule))
        if any(digest(getattr(args,name))!=value for name,value in bindings.items()) or any(
                digest(paths[r['id']])!=r['audio_sha256'] for r in panel):
            raise ValueError('Bound input changed during inference')
        result.update(completed=True,arms=arms,
            admission_development=admission_opportunities(vectors,panel,0,args.baseline_threshold,args.margin),
            admission_heldout=admission_opportunities(vectors,panel,15,args.baseline_threshold,args.margin))
    except Exception as error:
        result['error']=type(error).__name__+': '+str(error)
        raise
    finally:
        result['elapsed_seconds']=time.monotonic()-started
        (args.out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result),flush=True)


if __name__=='__main__':
    main()
