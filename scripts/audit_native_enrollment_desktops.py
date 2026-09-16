"""Independent readback of the three desktop enrollment/voice proof artifacts.

It does not turn recorded-speech evidence into signed, installed or human-level
qualification. Timing is host-observed warm synthesis, not an acoustic measure.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import wave
import zipfile


def digest(raw):return hashlib.sha256(raw).hexdigest()


def sdk_identity(result, pin):
    """A completed target gate, not a prepared flag, establishes SDK closure."""
    assert result['passed'] and result['exit_code']==0
    if result['sdk_candidate_only']:
        assert result['release_dependency_qualified'] is False
        assert result['sdk_revision']==pin['revision']+'+isolated-confirmation-serialization-candidate'
        return False
    assert result['sdk_source_landed'] is True
    assert result['release_dependency_qualified'] is True
    assert result['sdk_revision']==pin['revision'],'SDK revision differs from sealed source'
    assert result['sdk_source_archive_sha256']==pin['archive_sha256'],'SDK archive differs from sealed source'
    return True


def archive(path):
    z=zipfile.ZipFile(path)
    names=z.namelist();assert len(names)==len(set(names))
    assert all(not n.startswith('/') and '..' not in n.split('/') for n in names)
    rows=json.loads(z.read('manifest.json'))
    for name,row in rows.items():
        raw=z.read(name);assert len(raw)==row['bytes'] and digest(raw)==row['sha256'],name
    return z,rows


def analyze(read,prefix,result):
    assert result['passed'] and result['exit_code']==0 and result['host_is_fixture']
    if result['sdk_candidate_only']:
        assert result['release_dependency_qualified'] is False
    else:
        assert result['sdk_source_landed'] and result['release_dependency_qualified']
        assert len(result['sdk_revision'])==40 and len(result['sdk_source_archive_sha256'])==64
    assert result['spoken_interruption_opening_words_and_recovery_passed'] and result['canonical_codec_verified']
    known=result['known_observation'];unknown=result['unknown_observation']
    assert known['decision']=='known' and known['native_evidence']['speaker_id']=='237'
    assert unknown['decision']=='unknown' and not unknown['speaker']
    for obs in (known,unknown):assert obs['used_for_permissions'] is False and obs['late']
    raw=read(prefix+'public-enrollment.snapshot.json');assert digest(raw)==result['public_enrollment_sha256']
    snapshot=json.loads(raw);assert snapshot['revision']==1 and len(snapshot['speakers'])==1
    assert len(snapshot['speakers'][0]['samples'])==3
    assert sorted(s['audio_sha256'] for s in snapshot['speakers'][0]['samples'])==result['selected_final_pcm_digests']
    listed=[c['reply']['result']['operation_result'] for c in result['cases'] if 'result' in c['reply'] and 'eligible_final_sequences' in c['reply']['result'].get('operation_result',{})]
    assert any(x['eligible_final_sequences']==result['finals_selected'] for x in listed)
    assert len(result['finals_selected'])==len(set(result['finals_selected']))==3
    refused=[c for c in result['cases'] if 'error' in c['reply']];assert len(refused)>=5
    unresolved=[c['reply']['result'] for c in result['cases'] if c['reply'].get('result',{}).get('status')=='failed']
    assert any(x['operation_result']['publication']['readback_verified'] and not x['operation_result']['speakers'] for x in unresolved)
    speech_raw=read(prefix+'spoken-regression/report.json');assert digest(speech_raw)==result['spoken_regression_sha256']
    speech=json.loads(speech_raw)
    assert speech['passed'] and not speech['fixture_models'] and speech['interruption_mode']=='recorded_speech_vad'
    normalize=lambda s:''.join(c.lower() if c.isalnum() else ' ' for c in s).split()
    assert normalize(speech['transcript'])==normalize(speech['expected_transcript'])
    events=speech['events'];timings=[]
    for end in [e for e in events if e['type']=='synthesis_end']:
        start=next(e for e in events if e['type']=='synthesis_start' and e['synthesis_id']==end['synthesis_id'])
        stream=end['output_stream'];frames=[f for f in speech['frame_spans'] if f['stream']==stream]
        pcm=[f for f in frames if f['kind']==1]
        count=0
        for frame in pcm:assert frame['start']==count;count+=frame['samples']
        assert count==end['delivered_samples']==end['generated_samples'] and count>0
        with wave.open(io.BytesIO(read(prefix+f'spoken-regression/output-stream-{stream}.wav'))) as w:
            assert (w.getframerate(),w.getnchannels(),w.getsampwidth(),w.getnframes())==(24000,1,2,count)
            raw_pcm=w.readframes(count)
            assert len(raw_pcm)==count*2 and any(raw_pcm),'missing or silent PCM tail'
        seconds=count/24000
        timings.append({'synthesis_id':end['synthesis_id'],'audio_seconds':seconds,
                        'first_pcm_from_synthesis_start_ms':1000*(pcm[0]['elapsed']-start['elapsed']),
                        'generation_to_delivery_rtf':(end['elapsed']-start['elapsed'])/seconds})
    assert len(timings)==2
    return {'passed':True,'startup_seconds':result['readiness']['host_startup_timing']['spawn_to_ready_seconds'],
            'accelerator':result['readiness']['accelerator'],'known_score':known['score'],'unknown_score':unknown['score'],
            'held_storage_three_control_ms':result['held_storage_control_seconds']*1000,'warm_synthesis':timings,
            'spoken_words':speech['transcript'],'enrollment_sha256':digest(raw)}


def main():
    p=argparse.ArgumentParser()
    for name in ('mac','linux','windows','source','out'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();assert not a.out.exists()
    source,source_files=archive(a.source)
    pin=json.loads(source.read('plugin/sdk-source.json'))
    assert digest(source.read(pin['archive']))==pin['archive_sha256']
    current=[n for n in source_files if n.startswith(('runtime/native/session/','runtime/native_uid/','plugin/native/'))]
    report={'passed':False,'scope':__doc__,'signed':False,'installed':False,'human_level_qualified':False,
            'sdk_candidate_only':True,'release_dependency_qualified':False,'platforms':{},'common_source_files':len(current),'archives':{}}
    mac=json.loads((a.mac/'result.json').read_text())
    landed=sdk_identity(mac,pin)
    report.update(sdk_candidate_only=not landed,release_dependency_qualified=landed,sdk_revision=pin['revision'])
    report['platforms']['macos']=analyze(lambda n:(a.mac/n).read_bytes(),'',mac)
    root=Path(__file__).resolve().parents[1]
    for n in current:
        assert mac['bindings'][str(root/n)]==source_files[n]['sha256'],('Mac source differs',n)
    for platform,path,prefix in (('linux',a.linux,''),('windows',a.windows,'run/')):
        z,rows=archive(path);r=json.loads(z.read(prefix+'sdk/result.json'));prep=json.loads(z.read(prefix+'preparation.json'))
        assert sdk_identity(r,pin)==landed,'mixed candidate and landed SDK evidence'
        retirement=json.loads(z.read('retirement.json'));assert retirement['absent_pids']
        assert r['loaded_worker']['pid'] in retirement['absent_pids'] and r['loaded_worker']['parent_pid'] in retirement['absent_pids']
        assert json.loads(z.read(prefix+'complete.json'))['passed']
        src=prep['sdk_candidate'].replace('\\','/').rsplit('/.build/',1)[0]
        bindings={path.replace('\\','/'):h for path,h in prep['bindings'].items()}
        # Compare the current source root, never a matching old dependency copy.
        for n in current:
            assert bindings[src+'/'+n]==source_files[n]['sha256'],('desktop source differs',platform,n)
        report['platforms'][platform]=analyze(z.read,prefix+'sdk/',r)
        report['archives'][platform]={'sha256':digest(path.read_bytes()),'files':len(rows),'absent_pids':retirement['absent_pids']}
    report['source_sha256']=digest(a.source.read_bytes());report['passed']=True
    a.out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
