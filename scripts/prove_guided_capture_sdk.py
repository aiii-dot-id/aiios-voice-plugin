"""Real five-model guided capture -> durable handle -> restarted SDK enrollment.

Recorded public speech and a disk-backed fixture broker, NOT a physical consent
UI, installed containment or power-loss qualification. No real identity changes.
The fixture's durable receipts simulate the already separately tested host API.
"""
import argparse, base64, hashlib, json, os, queue, re, shlex, struct, sys, threading, time, traceback, wave
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from scripts.build_plugin_carrier import verify_build, SDK_SOURCE
from scripts.prove_plugin_sdk_engine import SDKHost
from runtime.plugin_engine.audio import Frame, PCM, END

ROOT=Path(__file__).resolve().parents[1]
def digest(data): return hashlib.sha256(data).hexdigest()
def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

class Broker:
    def __init__(self, host, root):
        self.host,self.root=host,root; root.mkdir(exist_ok=True)
        self.stop=threading.Event();self.errors=[];self.calls=[]
        self.durable=True;self.conflict=False
        self.hold=threading.Event();self.hold.set();self.entered=threading.Event()
        self.thread=threading.Thread(target=self.serve);self.thread.start()
    def serve(self):
        try:
            while not self.stop.is_set():
                try: q=self.host.host_requests.get(timeout=.05)
                except queue.Empty: continue
                p=q['params'];op=p['operation'];target=p['target'];a=p['arguments'];name=target['path']
                assert target['root']=='private'
                permanent=name in ('uid/enrollment.json','uid/captures.json')
                assert permanent or re.fullmatch(r'uid/\.(enrollment|captures)-[a-f0-9]{64}\.pending',name)
                path=self.root/Path(name).name
                self.entered.set()
                while not self.hold.wait(.01):
                    if self.stop.is_set(): return
                if op=='fs.read':
                    assert permanent and set(a)=={'offset','length','digest'}
                    if not path.exists(): value={'status':'failed','reasonCode':'FS_NOT_FOUND'}
                    else:
                        raw=path.read_bytes();offset=a['offset'];assert 0<=offset<=len(raw)
                        data=raw[offset:offset+a['length']]
                        v={**target,'offset':offset,'size':len(raw),'bytes':len(data),'eof':offset+len(data)==len(raw),'data_b64':base64.b64encode(data).decode()}
                        if a['digest']: v['sha256']=digest(raw)
                        value={'status':'succeeded','operation_result':v}
                elif op=='fs.write':
                    assert not permanent and set(a)=={'data_b64','append'}
                    raw=base64.b64decode(a['data_b64'],validate=True);assert 0<len(raw)<=65536
                    path.write_bytes((path.read_bytes() if a['append'] and path.exists() else b'')+raw)
                    value={'status':'succeeded','operation_result':{**target,'bytes':len(raw),'size':path.stat().st_size,'appended':a['append']}}
                elif op=='fs.publish':
                    assert permanent and set(a) in ({'from','sha256','expected_absent'},{'from','sha256','expected_sha256'})
                    prefix='captures' if name=='uid/captures.json' else 'enrollment'
                    assert re.fullmatch(r'uid/\.'+prefix+r'-[a-f0-9]{64}\.pending',a['from'])
                    source=self.root/Path(a['from']).name;raw=source.read_bytes();assert digest(raw)==a['sha256']
                    before=path.read_bytes() if path.exists() else None
                    mismatch=(before is not None if a.get('expected_absent') else before is None or digest(before)!=a['expected_sha256'])
                    if mismatch or self.conflict: value={'status':'failed','reasonCode':'FS_GENERATION_MISMATCH'}
                    else:
                        source.replace(path)
                        value={'status':'succeeded','operation_result':{**target,'size':len(raw),'sha256':digest(raw),'replaced':before is not None,'durable':self.durable,'durability':'synced' if self.durable else 'unknown'}}
                else: raise AssertionError(op)
                self.calls.append({'operation':op,'path':name,'status':value['status']})
                raw=json.dumps({'jsonrpc':'2.0','id':q['id'],'result':value}).encode()
                with self.host.write_lock:
                    self.host.process.stdin.write(struct.pack('>I',len(raw))+raw);self.host.process.stdin.flush()
        except BaseException as e: self.errors.append(repr(e))
    def close(self):
        self.stop.set();self.hold.set();self.thread.join(2)
        assert not self.thread.is_alive() and not self.errors,self.errors

def main():
    p=argparse.ArgumentParser();p.add_argument('--build',type=Path,required=True);p.add_argument('--carrier-build',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--checkpoint',type=Path,help='Exact packaged native desktop runtime; no private worker command')
    p.add_argument('--uid-contract',type=Path,help='Explicit hash-bound alternative model assessment; same protocol and acceptance')
    p.add_argument('--artifact-root', type=Path, required=True)
    a=p.parse_args()
    artifacts=a.artifact_root.resolve()
    a.out=a.out.resolve();a.out.mkdir(parents=True,exist_ok=False);a.build=a.build.resolve()
    report={'passed':False,'scope':__doc__,'signed':False,'installed':False,'bindings':{},'cycles':[]}
    host=broker=None
    def bind(path):
        path=Path(path).resolve();report['bindings'][str(path)]=sha(path);return path
    def save(): (a.out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    try:
        bind(__file__);bind(ROOT/'scripts/prove_plugin_sdk_engine.py')
        for directory in ('runtime/native/session','runtime/native_uid','plugin/native'):
            for path in (ROOT/directory).rglob('*'):
                if path.is_file(): bind(path)
        checkpoint=a.checkpoint.resolve() if a.checkpoint else None
        if checkpoint:
            from scripts.native_checkpoint_binding import verify_checkpoint
            checkpoint_binding=verify_checkpoint(checkpoint);frozen,record,_,bindings=checkpoint_binding
            for name,h in bindings.items(): assert sha(bind(name))==h
            windows=frozen.get('platform')=='windows';backend=frozen.get('backend','cpu')
            carrier=bind(checkpoint/'runtime'/('aii-voice-t3.exe' if windows else 'aii-voice-t3'))
            uid=bind(Path(frozen['models_root'])/'uid/model.onnx');report['sdk_revision']=record['sdk_revision']
            report['checkpoint']={'root':str(checkpoint),'runtime_manifest_sha256':frozen['runtime_manifest_sha256'],'platform':frozen.get('platform','macos')}
        else:
            built=verify_build(a.carrier_build);report['sdk_revision']=built['sdk_revision']
            carrier=bind(a.carrier_build/'aii-voice-t3-race');bind(a.carrier_build/'build.json')
            binary=bind(a.build/'aii_voice_worker');bind(a.build/'libaii_voice_runtime.dylib');bind(a.build/'CMakeCache.txt')
            for depfile in a.build.rglob('*.o.d'):
                for token in shlex.split(depfile.read_text().replace('\\\n',''))[1:]:
                    dep=Path(os.path.normpath(token))
                    if dep.is_file() and dep.is_relative_to(ROOT): bind(dep)
            for line in (a.build/'CMakeCache.txt').read_text().splitlines():
                if line.startswith(('AII_ASR_LIBRARY:','AII_UID_LIBRARY:')): bind(line.split('=',1)[1])
            core=json.loads(bind(artifacts/'deliverables/native-c-embedding-20260912-r2/result.json').read_text())
            for name,row in core['models'].items(): assert sha(bind(name))==row['sha256']
            uid=bind(artifacts/'artifacts/wespeaker-uid/voxblink2_samresnet34_ft.onnx');backend='cpu'
        report['fixture_interpreter']={'path':sys.executable,'version':sys.version}
        if a.uid_contract:
            assert checkpoint,'an alternative UID assessment requires an exact packaged checkpoint'
            from scripts.guided_uid_evidence import load_contract
            bind(ROOT/'scripts/guided_uid_evidence.py')
            policy,assessment,assessed,embedding_reference=load_contract(a.uid_contract,uid,bind)
        else:
            baseline=bind(artifacts/'deliverables/speaker-identity/installed-assets-20260910-r1/relocated-resources/uid-policy.json')
            assessment=bind(artifacts/'deliverables/uid-guided-capture-assessment-20260915-r1/result.json')
            assessed=json.loads(assessment.read_text());assert assessed['assessment_complete'] and not assessed['production_changed']
            policy=json.loads(baseline.read_text());policy['minimum_enrollment_samples']=1;policy['calibration_sha256']=sha(assessment)
            embedding_reference=assessment.parent/'embeddings.json'
        policy_path=a.out/'guided-test-policy.json';policy_path.write_text(json.dumps(policy,sort_keys=True,separators=(',',':')));bind(policy_path)
        if checkpoint:
            native=json.loads(bind(checkpoint/'runtime/native-profile.json').read_text())
            assert sha(bind(checkpoint/'runtime'/native['uid_policy']))==sha(policy_path),'packaged guided policy differs from assessed policy'
        panel=bind(artifacts/'deliverables/speaker-identity/fresh-speakers-panel-20260909-r1/manifest.json')
        assert sha(panel)=='e0b51eac7c7121d0f38b96fcf9e1948cdb60493a9c60ad498e6557382d5bedd6'
        raw=bytearray()
        for row in json.loads(panel.read_text())['samples']:
            if row['role']!='enrollment' or row['speaker_id']!=237: continue
            path=bind(panel.parent/'audio'/row['audio_file']);assert sha(path)==row['audio_sha256']
            with wave.open(str(path),'rb') as w:
                assert (w.getnchannels(),w.getsampwidth(),w.getframerate())==(1,2,16000)
                raw.extend(w.readframes(w.getnframes()))
        assert digest(raw)==assessed['captures'][0]['pcm_sha256']
        samples=len(raw)//2;report['recording']={'samples':samples,'sha256':digest(raw),'source':'three public excerpts, one test capture; not a physical recording'}
        command=None if checkpoint else [str(binary),*core['command'][1:8],backend,str(uid),str(policy_path)]
        def start(index):
            nonlocal host,broker
            out=a.out/f'cycle-{index}';out.mkdir()
            cfg=SimpleNamespace(output=out,carrier=carrier,fixture=False,backend='native-common-'+backend,stage=None,operator_settings={'turn_pause_ms':768},extra_host_operations=['fs.read','fs.write','fs.publish'],sdk_source=SDK_SOURCE)
            if checkpoint:
                cfg.packaged_runtime=True;cfg.runtime_manifest_sha=frozen['runtime_manifest_sha256'];cfg.model_data_root=Path(frozen['models_root'])
            host=SDKHost(cfg,worker_command=command);ready=host.readiness();assert ready['models_loaded']==5
            broker=Broker(host,a.out/'fixture-private');report['cycles'].append({'pid':host.process.pid,'ready':ready,'launch_command':host.launch_command})
            if checkpoint: assert host.launch_command==[str(carrier)]
            if checkpoint and frozen.get('platform') in ('windows','linux'):
                from scripts.native_loaded_images import observe
                report['cycles'][-1]['loaded_worker']=observe(host.process.pid,checkpoint/'runtime/bin',frozen['platform'],frozen['library_hashes'])
        def stop():
            nonlocal host,broker
            broker.close();report['cycles'][-1]['broker_calls']=broker.calls
            report['cycles'][-1]['events']=host.events;report['cycles'][-1]['controls']=host.calls
            report['cycles'][-1]['exit_code']=host.close();assert report['cycles'][-1]['exit_code']==0
            host=broker=None;save()
        def speaker(op,args,confirm=False,refused=False):
            host.counter+=1;key=host.counter;args=dict(args);args['_host_now_ms']=int(time.time()*1000)
            if confirm: args['_host_operator_act']={'id':f'capture-test-{key}','confirmed_at':datetime.now(timezone.utc).isoformat()}
            request={'jsonrpc':'2.0','id':key,'method':'invoke.call','params':{'operation':'speaker.'+op,'arguments':args}}
            raw_request=json.dumps(request).encode()
            with host.write_lock: host.process.stdin.write(struct.pack('>I',len(raw_request))+raw_request);host.process.stdin.flush()
            reply=host.responses.get(timeout=45);assert reply['id']==key,reply
            report.setdefault('management',[]).append(reply)
            assert ('error' in reply)==refused,reply
            return reply.get('result')
        def open_capture(sid,nonce):
            return host.call('open',{'session_id':sid,'input_handle':'mic','output_handle':'speaker','audio':{'format':'s16le','input':{'rate':16000,'channels':1},'output':{'rate':24000,'channels':1}},'enrollment_capture':{'consented':True,'request_id':nonce,'created_ms':int(time.time()*1000)}})
        start(1)
        assert speaker('list',{})['operation_result']['pending_captures']==[]
        assert open_capture('explicit',digest(b'explicit'))['purpose']=='enrollment_capture'
        host.event('session_ready',session_id='explicit')
        # Declare Finish before the final tail arrives. The complete tail must
        # survive; the host's request return is not a fabricated completion.
        host.call('finish_input',{'session_id':'explicit','stream_id':'mic','end_sample':samples})
        for seq,start_sample in enumerate(range(0,samples,1024),1):
            chunk=bytes(raw[start_sample*2:min(samples,start_sample+1024)*2])
            host.to_engine.write(Frame(PCM,1,seq,start_sample,chunk).encode())
        host.to_engine.write(Frame(END,1,seq+1,samples).encode())
        host.call('close',{'session_id':'explicit','mode':'drain'})
        final=host.event('session_end',session_id='explicit',timeout=45)
        assert final['status']=='completed' and final['input_samples']==samples and final['model_padding_samples']==0
        captured=final['enrollment_capture'];assert captured['state']=='retained'
        assert not host.frames and not any(e['type'].startswith('transcript') for e in host.events)
        listed=speaker('list',{})['operation_result'];assert not listed['session_open'] and len(listed['pending_captures'])==1
        capture_id=captured['capture_id'];assert listed['pending_captures'][0]['capture_id']==capture_id
        assert not (a.out/'fixture-private/enrollment.json').exists()
        report['capture_id']=capture_id
        # Hold storage while a second preparation reaches it. Abort must not
        # wait behind the broker, and cancelled work must not retain new bytes.
        before=(a.out/'fixture-private/captures.json').read_bytes()
        open_capture('abort-held',digest(b'abort-held'));host.event('session_ready',session_id='abort-held')
        broker.entered.clear();broker.hold.clear()
        for seq,start_sample in enumerate(range(0,samples,1024),1):
            host.to_engine.write(Frame(PCM,2,seq,start_sample,bytes(raw[start_sample*2:min(samples,start_sample+1024)*2])).encode())
        host.to_engine.write(Frame(END,2,seq+1,samples).encode())
        assert broker.entered.wait(15)
        started=time.perf_counter();host.call('close',{'session_id':'abort-held','mode':'abort'})
        report['held_broker_abort_admission_seconds']=time.perf_counter()-started
        assert report['held_broker_abort_admission_seconds']<.75
        host.event('session_end',session_id='abort-held',timeout=10)
        broker.hold.set();assert (a.out/'fixture-private/captures.json').read_bytes()==before
        stop()
        start(2)
        listed=speaker('list',{})['operation_result'];assert not listed['session_open'] and listed['session_id']==''
        assert listed['pending_captures'][0]['capture_id']==capture_id
        args={'capture_id':capture_id,'speaker_id':'237','label':'Public evaluation speaker 237'}
        speaker('enroll',args,refused=True)
        speaker('enroll',{**args,'capture_id':'0'*64},confirm=True,refused=True)
        enrolled=speaker('enroll',args,confirm=True)
        assert enrolled['status']=='succeeded' and enrolled['operation_result']['enrollment_durable'] and enrolled['operation_result']['capture_retirement_durable']
        listed=speaker('list',{})['operation_result'];assert listed['pending_captures']==[]
        assert listed['speakers']==[{'speaker_id':'237','label':args['label'],'recordings':1,'ready':True}]
        assert not any(e['type']=='session_start' for e in host.events)
        report['after_restart_confirmation']=enrolled
        stop()
        # Compare storage against an independent ABI call on this platform.
        # A Mac floating-point reference cannot prove Windows byte fidelity.
        profile=json.loads((a.out/'fixture-private/enrollment.json').read_text())
        assert profile['revision']==1 and len(profile['speakers'])==1
        sample=profile['speakers'][0]['samples'][0]
        assert sample['audio_sha256']==report['recording']['sha256']
        vector=struct.unpack('<256d',base64.b64decode(sample['embedding_f64le_b64'],validate=True))
        reference=json.loads(bind(embedding_reference).read_text())['237']
        norm=sum(x*x for x in reference)**.5;reference=[x/norm for x in reference]
        report['persisted_embedding_max_reference_delta']=max(abs(x-y) for x,y in zip(vector,reference))
        if checkpoint:
            from scripts.guided_capture_reference import embed, compare, library_member
            bind(ROOT/'scripts/guided_capture_reference.py')
            inventory=json.loads((checkpoint/'runtime/voice-runtime.json').read_text())['files']
            library=bind(checkpoint/'runtime'/library_member(inventory,frozen.get('platform','macos')))
            direct=embed(library,uid,raw)
            report['independent_local_reference']={'library':str(library),'model':str(uid),
                'pcm_sha256':digest(raw),'embedding':direct,'method':'direct native UID ABI, outside carrier/session/storage path'}
            report['embedding_checks']=compare(vector,direct,reference)
        else:
            assert report['persisted_embedding_max_reference_delta']<1e-12
        report['profile_sha256']=sha(a.out/'fixture-private/enrollment.json')
        report['profile']=profile
        assert all(sha(path)==value for path,value in report['bindings'].items()),'bound input changed'
        if checkpoint: assert verify_checkpoint(checkpoint)==checkpoint_binding
        report['passed']=True
    except BaseException as e: report['error']=repr(e);report['traceback']=traceback.format_exc();raise
    finally:
        if broker: broker.close()
        if host: report['cleanup_exit']=host.close()
        save()
    print(json.dumps({'passed':True,'capture_id':report['capture_id'],'after_restart':True,'abort_admission_seconds':report['held_broker_abort_admission_seconds']}),flush=True)
if __name__=='__main__': main()
