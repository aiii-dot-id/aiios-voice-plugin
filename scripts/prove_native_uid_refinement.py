"""Four same-speaker PCM16/16k recordings through the real resident SDK lane.

Private output contains recorded transcripts. No installed identity is accessed.
First two recordings establish one profile; both later recordings must match it.
This is a regression gate, not unknown-speaker or general accuracy qualification.
"""
from scripts._assertions import require_assertions
require_assertions()
import argparse,json,os,time
from pathlib import Path
from types import SimpleNamespace
os.umask(0o077)
os.environ['ORT_DISABLE_TELEMETRY']='1'
from scripts.native_checkpoint_binding import verify_checkpoint,sha
from scripts.prove_plugin_sdk_engine import SDKHost
from scripts.build_plugin_carrier import SDK_SOURCE
from scripts.speaker_registry_test_host import RegistryBroker,operation
from scripts.plugin_receipt_probe import receipt
from runtime.plugin_engine.audio import Frame,PCM,END
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--checkpoint',type=Path,required=True)
p.add_argument('--out',type=Path,required=True)
p.add_argument('--recordings',type=Path,nargs=4,required=True,help='Ordered mono PCM16 little-endian, 16 kHz; one speaker')
p.add_argument('--nonmatching-recordings',type=Path,nargs='*',default=[],help='Additional bounded PCM16 recordings containing none of the acquired speaker')
a=p.parse_args();checkpoint=a.checkpoint.resolve();out=a.out.resolve();out.mkdir(mode=0o700,parents=True,exist_ok=False)
for path in [*a.recordings,*a.nonmatching_recordings]:
    if path.is_symlink() or not path.is_file() or not 64000<=path.stat().st_size<=32*16000*2 or path.stat().st_size%2:
        raise ValueError('bounded mono PCM16 recording required')
recordings=[path.resolve() for path in a.recordings]
nonmatching=[path.resolve() for path in a.nonmatching_recordings]
frozen,record,_,bindings=verify_checkpoint(checkpoint)
bindings.update({str(path):sha(path) for path in [*recordings,*nonmatching]})
bindings[str(Path(__file__).resolve())]=sha(Path(__file__))
assert len({bindings[str(path)] for path in recordings})==4,'four distinct recordings required'
report=dict(passed=False,scope=__doc__,installed=False,browser_tested=False,
            uid_accuracy_qualified=False,
            runtime_manifest_sha256=frozen['runtime_manifest_sha256'],
            carrier_sha256=frozen['carrier_sha256'],cases=[],processes_retired=False)
host=broker=None;storage={};expected=None;begun=time.monotonic()
def save():
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
def start(index):
    global host,broker
    path=out/('owner-'+str(index));path.mkdir()
    cfg=SimpleNamespace(output=path,carrier=checkpoint/'runtime'/('aii-voice-t3.exe' if os.name=='nt' else 'aii-voice-t3'),fixture=False,
        backend='native-common-'+frozen.get('backend','cpu'),stage=None,sdk_source=SDK_SOURCE,packaged_runtime=True,
        runtime_manifest_sha=frozen['runtime_manifest_sha256'],model_data_root=Path(frozen['models_root']),
        operator_settings={'tts_voice':'alba','turn_pause_ms':5000},extra_host_operations=['fs.read','fs.write','fs.publish'])
    host=SDKHost(cfg);broker=RegistryBroker(host,storage)
    ready=host.readiness();assert ready['models_loaded']==5
    report.setdefault('readiness',[]).append(ready);save()
    print(json.dumps(dict(phase='ready',process=index,seconds=ready['observed_elapsed'])),flush=True)
def stop(index):
    global host,broker
    (out/('events-'+str(index)+'.json')).write_text(json.dumps(host.events))
    assert host.close()==0 and host.process.poll() is not None
    broker.close();host=broker=None
def speech(index,path,*,interrupt=False,known=False,nonmatch=False):
    sid='fresh-'+str(index);raw=path.read_bytes();samples=len(raw)//2
    assert sha(path)==bindings[str(path)],'recording changed since binding'
    host.call('open',dict(session_id=sid,input_handle='mic',output_handle='speaker',
        audio=dict(format='s16le',input=dict(rate=16000,channels=1),output=dict(rate=24000,channels=1))))
    host.event('session_ready',session_id=sid)
    if interrupt:
        host.call('synthesize',dict(session_id=sid,synthesis_id=sid+'-tts',text='A recorded speech test can interrupt this reply. '*30))
        event=host.event('synthesis_start',sid+'-tts',session_id=sid)
        host.first_pcm(event['output_stream'],timeout=40)
    begin=time.monotonic()
    for seq,offset in enumerate(range(0,samples,512),1):
        host.to_engine.write(Frame(PCM,7,seq,offset,raw[2*offset:2*(offset+512)]).encode())
        time.sleep(max(0,min(offset+512,samples)/16000-(time.monotonic()-begin)))
    finishing=time.perf_counter()-host.started
    host.call('finish_input',dict(session_id=sid,stream_id='mic',end_sample=samples))
    host.to_engine.write(Frame(END,7,seq+1,samples).encode())
    if interrupt:
        host.event('interruption_requested',sid+'-tts',session_id=sid)
        cancelled=host.event('synthesis_cancelled',sid+'-tts',timeout=30,session_id=sid)
        receipt(host,sid,cancelled,stopped=True)
    completed=host.event('input_finished',timeout=90,session_id=sid)
    assert completed['end_sample']==completed['processed_end_sample']==samples
    finals=[e for e in host.events if e.get('session_id')==sid and e['type']=='transcript_final']
    assert finals and all(e.get('text') for e in finals)
    # Input completion ends transcription, not the asynchronous identity job.
    # Wait for each exact join, bounded by the existing final-to-identity limit.
    deadline=max(f['elapsed'] for f in finals)+3
    while True:
        observations=[e for e in host.events if e.get('session_id')==sid and e['type']=='speaker_observation']
        if len(observations)>=len(finals):break
        failures=[e for e in host.events if e.get('session_id')==sid and e['type']=='failure']
        assert not failures,'engine failed while awaiting speaker observations'
        if not host.errors.empty():raise RuntimeError(host.errors.get())
        assert host.process.poll() is None,'carrier ended before speaker observations'
        assert time.perf_counter()-host.started<deadline,'speaker observation missing at three-second join deadline'
        time.sleep(0.005)
    assert len(observations)==len(finals),'speaker observation count differs from finalized tracks'
    for final in finals:
        joined=[e for e in observations if e.get('refers_to')==final['sequence'] and e.get('track_id')==final.get('track_id')]
        assert len(joined)==1 and final.get('track_id'),'missing exact final/identity join'
    uuids={e['speaker_uuid'] for e in observations if e.get('speaker_uuid')}
    if known:assert all(e.get('speaker_uuid')==expected for e in observations),'known recorded voice did not resolve to its acquired UUID'
    if nonmatch:assert expected not in uuids,'another speaker was falsely attributed to the acquired UUID'
    elif expected:assert not uuids or uuids=={expected},'same speaker acquired a different UUID'
    if interrupt:
        host.call('synthesize',dict(session_id=sid,synthesis_id=sid+'-recovery',text='Recovery is complete.'))
        end=host.event('synthesis_end',sid+'-recovery',timeout=60,session_id=sid)
        assert end['delivered_samples']>0
        host.call('close',dict(session_id=sid,mode='drain'))
        assert host.call('status',dict(session_id=sid))['lifecycle']=='draining'
        receipt(host,sid,end)
    else:host.call('close',dict(session_id=sid,mode='drain'))
    assert host.event('session_end',session_id=sid)['status']=='completed'
    row=dict(case=index,nonmatching_control=nonmatch,input_samples=samples,finals=len(finals),observations=len(observations),
             resolved=sum(bool(e.get('speaker_uuid')) for e in observations),distinct_uuids=len(uuids),
             reasons=[e.get('reason') for e in observations],interruption_and_recovery=interrupt,
             finish_to_input_complete_seconds=completed['elapsed']-finishing,
             final_to_identity_seconds=[e['elapsed']-next(f['elapsed'] for f in finals if f['sequence']==e['refers_to']) for e in observations],
             identity_delivery_deadline_seconds=3)
    assert all(0<=elapsed<=3 for elapsed in row['final_to_identity_seconds']),'speaker observation missed three-second join gate'
    report['cases'].append(row);save();print(json.dumps(row),flush=True)
    return observations
try:
    start(0)
    assert operation(host,'speaker.buckets',{})['speakers']==[] and not storage
    speech(0,recordings[0],interrupt=True)
    speech(1,recordings[1])
    listed=operation(host,'speaker.buckets',{})
    assert len(listed['speakers'])==1,'one test speaker must acquire one UUID'
    expected=listed['speakers'][0]['speaker_uuid']
    named=operation(host,'speaker.associate',dict(speaker_uuid=expected,registry_revision=listed['registry_revision'],display_label='Test Speaker'),confirmed=True)
    assert named['speakers'][0]['display_label']=='Test Speaker'
    speech(2,recordings[2],known=True)
    preserved=storage['uid/speakers.json']
    stop(0);start(1)
    listed=operation(host,'speaker.buckets',{})
    assert listed['speakers'][0]['speaker_uuid']==expected and listed['speakers'][0]['display_label']=='Test Speaker'
    assert storage['uid/speakers.json']==preserved
    speech(3,recordings[3],known=True)
    assert storage['uid/speakers.json']==preserved
    for index,path in enumerate(nonmatching,4):
        speech(index,path,nonmatch=True)
    stop(1)
    assert all(sha(Path(path))==value for path,value in bindings.items())
    report.update(passed=True,processes_retired=True,fresh_profile_acquired=True,
                  label_assigned_post_session=True,uuid_and_label_survive_process_restart=True,
                  recognition_did_not_rewrite_profile=True)
except Exception as e:
    report['failure']=str(e)
    raise
finally:
    if host:
        (out/'failure-events.json').write_text(json.dumps(host.events))
        report['cleanup_exit']=host.close();broker.close()
    report['bindings']=bindings
    report['seconds']=time.monotonic()-begun;save()
