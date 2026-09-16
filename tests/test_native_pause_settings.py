import copy
import json
from pathlib import Path
import zipfile

import pytest

from scripts.prove_native_pause_settings import corrected_metadata, validate_pair, absent_enrollment_reply, ENROLLMENT_READS


def test_count_correction_changes_no_executable_or_other_metadata():
    p=Path(__file__).resolve().parents[1]/'deliverables/native-candidate-runtime-windows-20260913-r1/windows-evidence.zip'
    with zipfile.ZipFile(p) as z:
        old=json.loads(z.read('run/checkpoint/freeze.json'))
        profile=json.loads(z.read('run/checkpoint/runtime/voice-runtime.json'))
    snapshot=copy.deepcopy(old);new=corrected_metadata(old,profile)
    assert old==snapshot
    assert {n for n in old if old[n]!=new[n]}=={'runtime_files'}
    assert new['runtime_files']==20
    with pytest.raises(ValueError):corrected_metadata(new,profile)


@pytest.mark.parametrize('damage',[None,'same-boundary','different-words','missing-opening','finish','wrong-effective'])
def test_pause_effect_not_just_a_setting_readback(damage):
    cases=[{'pause_ms':ms,'effective':{'turn_pause_ms':ms},'reason':'semantic_pause',
            'finals':[{'start_sample':0,'end_sample':end,'text':'Please keep my opening words.'}],
            'total_samples':160000} for ms,end in ((1024,60000),(3072,92768))]
    if damage=='same-boundary':cases[1]['finals'][0]['end_sample']=60000
    if damage=='different-words':cases[1]['finals'][0]['text']='Different words entirely.'
    if damage=='missing-opening':cases[1]['finals'][0]['start_sample']=512
    if damage=='finish':cases[1]['reason']='finish_input'
    if damage=='wrong-effective':cases[1]['effective']['turn_pause_ms']=1024
    if damage:
        with pytest.raises(AssertionError):validate_pair(cases)
    else:assert validate_pair(cases)['final_offset_difference_ms']==2048


@pytest.mark.parametrize('damage',[None,'write','path','offset'])
def test_pause_fixture_answers_only_absent_enrollment_read(damage):
    q={'id':7,'params':{'operation':'fs.read','target':{'root':'private','path':'uid/enrollment.json'},
                       'arguments':{'offset':0,'length':65536,'digest':True}}}
    if damage=='write':q['params']['operation']='fs.write'
    if damage=='path':q['params']['target']['path']='elsewhere'
    if damage=='offset':q['params']['arguments']['offset']=65536
    if damage:
        with pytest.raises(AssertionError):absent_enrollment_reply(q)
    else:assert absent_enrollment_reply(q)=={'jsonrpc':'2.0','id':7,'result':{'status':'failed','reasonCode':'FS_NOT_FOUND'}}


def test_automatic_uid_read_does_not_kill_control_reader():
    # Drive the actual helper through the formerly missed upstream message.
    import io,queue,struct,threading,time
    from types import SimpleNamespace
    from scripts.prove_plugin_sdk_engine import SDKHost
    upstream={'jsonrpc':'2.0','id':7,'method':'invoke.call','params':{'operation':'fs.read'}}
    answer={'jsonrpc':'2.0','id':3,'result':{'accepted':True}}
    def frame(body):
        raw=json.dumps(body).encode();return struct.pack('>I',len(raw))+raw
    for allowed in ((),ENROLLMENT_READS):
        host=SDKHost.__new__(SDKHost)
        host.process=SimpleNamespace(stdout=io.BytesIO(frame(upstream)+frame(answer)),stdin=io.BytesIO())
        host.started=time.perf_counter();host.events=[];host.errors=queue.Queue();host.responses=queue.Queue()
        host.host_requests=queue.Queue();host.write_lock=threading.Lock();host.operator_settings={};host.extra_host_operations=allowed
        host.control_reader()
        if allowed:
            assert host.host_requests.get_nowait()==upstream and host.responses.get_nowait()==answer
        else:
            assert host.responses.empty() and 'unexpected upstream call' in host.errors.get_nowait()


@pytest.mark.parametrize('damage',[None,'no-pause-effect','opening-lost','finish-only','invented-silence','wrong-read','incomplete-finish'])
def test_independent_pause_audit_rejects_false_closure(damage):
    from scripts.audit_native_pause_settings import observed
    p=Path(__file__).resolve().parents[1]/'deliverables/native-pause-effect-windows-20260913-r2/windows-evidence.zip'
    with zipfile.ZipFile(p) as z:r=json.loads(z.read('run/result.json'))
    if damage=='no-pause-effect':
        n=r['cases'][0]['finals'][0]['end_sample']
        r['cases'][1]['finals'][0]['end_sample']=n
        for e in r['events']:
            if e['session_id']=='pause-long' and e['type'] in ('transcript_final','turn_committed'):e['end_sample']=n
    elif damage=='opening-lost':
        r['cases'][1]['finals'][0]['start_sample']=512
        for e in r['events']:
            if e['session_id']=='pause-long' and e['type']=='transcript_final':e['start_sample']=512
    elif damage=='finish-only':
        r['cases'][1]['reason']='finish_input'
        for e in r['events']:
            if e['session_id']=='pause-long' and e['type']=='turn_committed':e['text']='finish_input'
    elif damage=='invented-silence':
        next(e for e in r['events'] if e['session_id']=='pause-silence' and e['type']=='session_start')['type']='transcript_final'
    elif damage=='wrong-read':r['broker_calls'][0]['request']['params']['operation']='fs.write'
    elif damage=='incomplete-finish':
        next(e for e in r['events'] if e['session_id']=='pause-long' and e['type']=='input_finished')['processed_end_sample']-=1
    if damage:
        with pytest.raises(AssertionError):observed(r)
    else:assert observed(r)['pause_delta_ms']==2048
