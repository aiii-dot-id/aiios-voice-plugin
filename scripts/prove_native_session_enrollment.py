"""Native recorded-final enrollment through actual SDK and an in-memory broker.

No operator enrollment, live identity, signing or installed-package changes.
The broker enforces the deployed host's fixed paths, CAS and publication shape.
"""
import argparse,base64,hashlib,json,os,queue,struct,threading,time,subprocess
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from scripts.build_plugin_carrier import verify_build,verify_sdk,SDK_SOURCE
from scripts.prove_plugin_sdk_engine import SDKHost,run
from scripts.plugin_receipt_probe import receipt
from runtime.plugin_engine.audio import PCM,END,Frame

ROOT=Path(__file__).resolve().parents[1]
def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def digest(data):return hashlib.sha256(data).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--build',type=Path,required=True);p.add_argument('--carrier-build',type=Path,required=True)
    p.add_argument('--sdk-candidate',type=Path,help='Explicit isolated SDK serializer correction; never a release-qualified dependency')
    p.add_argument('--prepared',type=Path,help='Sealed native desktop preparation; models remain on that desktop')
    p.add_argument('--checkpoint',type=Path,help='Frozen zero-argument native runtime; no development worker command')
    p.add_argument('--artifact-root', type=Path, required=True)
    a=p.parse_args()
    artifacts=a.artifact_root.resolve()
    a.out=a.out.resolve();a.out.mkdir(parents=True,exist_ok=False);a.build=a.build.resolve();a.carrier_build=a.carrier_build.resolve()
    r={'passed':False,'scope':__doc__,'platform':'macos','host_is_fixture':True,'human_level_qualified':False,'bindings':{},'cases':[]}
    def bind(path):path=Path(path);r['bindings'][str(path)]=sha(path);return path
    def save():(a.out/'result.json').write_text(json.dumps(r,indent=2)+'\n')
    host=None;stop=threading.Event();hold=threading.Event();hold.set();entered=threading.Event();storage={};broker_errors=[];calls=[];conflict=[False];durable=[True]
    try:
        # Fail before loading models if the retained speech fixture cannot be
        # resampled. Record this test-only environment, not runtime dependencies.
        import numpy, scipy
        r['fixture_environment']={'numpy':numpy.__version__,'scipy':scipy.__version__}
        prepared=json.loads(a.prepared.read_text()) if a.prepared else None
        candidate=a.sdk_candidate.resolve() if a.sdk_candidate else None
        checkpoint=a.checkpoint.resolve() if a.checkpoint else None
        if checkpoint:
            assert not prepared and not candidate,'one execution profile required'
            from scripts.native_checkpoint_binding import verify_checkpoint
            checkpoint_frozen,built,pin,bindings=verify_checkpoint(checkpoint)
            r['bindings'].update(bindings);r['sdk_revision']=pin['revision']
            r.update(sdk_candidate_only=False,sdk_source_landed=True,sdk_source_archive_sha256=pin['archive_sha256'],release_dependency_qualified=False)
            platform=checkpoint_frozen.get('platform','macos');windows=platform=='windows'
            r['platform']=platform
            carrier=checkpoint/'runtime'/('aii-voice-t3.exe' if windows else 'aii-voice-t3')
            worker=checkpoint/'runtime/bin'/('aii_voice_worker.exe' if windows else 'aii_voice_worker')
            policy=checkpoint/'runtime/resources/uid-policy.json';uid=Path(checkpoint_frozen['models_root'])/'uid/model.onnx'
            backend=checkpoint_frozen.get('backend','cpu');model_paths=[]
            r['checkpoint']={'root':str(checkpoint),'freeze_sha256':sha(checkpoint/'freeze.json'),'runtime_manifest_sha256':checkpoint_frozen['runtime_manifest_sha256']}
            bind(ROOT/'scripts/native_checkpoint_binding.py')
        elif prepared:
            assert not candidate and prepared['passed'] and prepared['platform'] in ('linux','windows')
            bind(a.prepared)
            for name,h in prepared['bindings'].items():assert sha(bind(name))==h,('prepared binding changed',name)
            r['platform']=prepared['platform'];r['preparation']=prepared
            r['sdk_revision']=prepared['sdk_revision'];r['sdk_candidate_only']=True;r['release_dependency_qualified']=False
            carrier=bind(prepared['carrier']);candidate=Path(prepared['sdk_candidate'])
            if prepared.get('sdk_source_landed'):
                landed_build_root=Path(prepared['verified_carrier_build'])
                built=verify_build(landed_build_root);pin,_=verify_sdk()
                assert prepared['sdk_revision']==built['sdk_revision']==pin['revision']
                assert prepared['sdk_source_archive_sha256']==pin['archive_sha256']
                assert sha(carrier)==built['artifacts'][carrier.name]['sha256']
                candidate=None
                r.update(sdk_candidate_only=False,sdk_source_landed=True,sdk_source_archive_sha256=pin['archive_sha256'])
            worker=bind(prepared['worker']);policy=bind(prepared['policy']);uid=bind(prepared['uid_model'])
            model_paths=prepared['model_paths'];backend=prepared['backend']
        elif candidate:
            pin,original=verify_sdk();r['sdk_revision']=pin['revision']+'+isolated-confirmation-serialization-candidate'
            r['sdk_candidate_only']=True;r['release_dependency_qualified']=False
            needle='\t\tout = append(out, \'}\')\n'
            fix='\t\tif d.OperatorConfirms {\n\t\t\tout = append(out, `,"operator_confirms":true`...)\n\t\t}\n'
            for name,h in original.items():
                path=bind(candidate/name)
                if name=='pkg/aiiosdk/descriptor.go':
                    before=(SDK_SOURCE/name).read_text();assert before.count(needle)==1
                    assert path.read_text()==before.replace(needle,fix+needle)
                else:assert sha(path)==h,('candidate changed unrelated SDK source',name)
            bind(candidate/'pkg/aiiosdk/confirmation_emission_test.go');bind(candidate/'carrier.mod')
            carrier=a.out/'aii-voice-t3-race'
            command=['/usr/local/go1.27/bin/go','build','-race','-modfile='+str(candidate/'carrier.mod'),'-o',str(carrier),'.']
            done=subprocess.run(command,cwd=ROOT/'plugin/native',capture_output=True,timeout=90)
            (a.out/'candidate-build.stdout').write_bytes(done.stdout);(a.out/'candidate-build.stderr').write_bytes(done.stderr);assert done.returncode==0
            r['candidate_build_command']=command;bind(carrier)
        else:
            built=verify_build(a.carrier_build);r['sdk_revision']=built['sdk_revision'];bind(a.carrier_build/'build.json')
            pin,_=verify_sdk()
            r['sdk_candidate_only']=False;r['sdk_source_landed']=True
            r['sdk_source_archive_sha256']=pin['archive_sha256']
            # Qualification is recorded only after the full gate retires.
            r['release_dependency_qualified']=False
            carrier=bind(a.carrier_build/'aii-voice-t3-race')
        if not prepared and not checkpoint:
            worker=bind(a.build/'aii_voice_worker');bind(a.build/'libaii_voice_runtime.dylib');bind(a.build/'CMakeCache.txt')
            models=json.loads(bind(artifacts/'deliverables/native-c-embedding-20260912-r2/result.json').read_text())
            for name,row in models['models'].items():assert sha(name)==row['sha256'];bind(name)
            model_paths=models['command'][1:8];backend='cpu'
            policy=bind(artifacts/'deliverables/speaker-identity/installed-assets-20260910-r1/relocated-resources/uid-policy.json')
            uid=bind(artifacts/'artifacts/wespeaker-uid/voxblink2_samresnet34_ft.onnx')
        for folder in ('runtime/native/session','runtime/native_uid','plugin/native'):
            for path in (ROOT/folder).rglob('*'):
                if path.is_file():bind(path)
        bind(Path(__file__));bind(ROOT/'scripts/prove_plugin_sdk_engine.py')
        cfg=SimpleNamespace(output=a.out/'owner',carrier=carrier,fixture=False,backend='native-common-'+backend,stage=None,operator_settings={'turn_pause_ms':768},
          extra_host_operations=['fs.read','fs.write','fs.publish'],sdk_source=candidate or SDK_SOURCE, recorded_input=artifacts/'deliverables/speech-output/validation-20260907-r2/recovery.wav')
        if checkpoint:
            cfg.packaged_runtime=True;cfg.runtime_manifest_sha=checkpoint_frozen['runtime_manifest_sha256'];cfg.model_data_root=Path(checkpoint_frozen['models_root'])
        cfg.output.mkdir();host=SDKHost(cfg,worker_command=None if checkpoint else [str(worker),*model_paths,backend,str(uid),str(policy)])
        r['launch_command']=host.launch_command
        if checkpoint:assert host.launch_command==[str(carrier)]
        r['readiness']=host.readiness();assert r['readiness']['models_loaded']==5;save()
        if prepared:
            from scripts.native_loaded_images import observe
            r['loaded_worker']=observe(host.process.pid,worker.parent,prepared['platform'],prepared['libraries']);save()
        elif checkpoint and r['platform'] in ('linux','windows'):
            from scripts.native_loaded_images import observe
            r['loaded_worker']=observe(host.process.pid,worker.parent,r['platform'],checkpoint_frozen['library_hashes']);save()
        def broker():
            try:
                while not stop.is_set():
                    try:q=host.host_requests.get(timeout=.05)
                    except queue.Empty:continue
                    params=q['params'];op=params['operation'];target=params['target'];args=params['arguments'];name=target['path']
                    # Ordinary discovery also checks for retained guided
                    # captures. This regression never writes that store.
                    assert target['root']=='private' and (name=='uid/enrollment.json' or
                        op=='fs.read' and name=='uid/captures.json' or
                        name.startswith('uid/.enrollment-') and name.endswith('.pending')),(op,name)
                    entered.set()
                    while not hold.wait(.02):
                        if stop.is_set():return
                    if op=='fs.read':
                        assert name in ('uid/enrollment.json','uid/captures.json') and set(args)=={'offset','length','digest'} and args['length']==65536,(op,name,sorted(args))
                        if name not in storage:value={'status':'failed','reasonCode':'FS_NOT_FOUND'}
                        else:
                            raw=storage[name];offset=args['offset'];data=raw[offset:offset+args['length']]
                            assert 0<=offset<=len(raw)
                            v={**target,'offset':offset,'size':len(raw),'bytes':len(data),'eof':offset+len(data)==len(raw),'data_b64':base64.b64encode(data).decode()}
                            if args['digest']:v['sha256']=digest(raw)
                            value={'status':'succeeded','operation_result':v}
                    elif op=='fs.write':
                        assert name!='uid/enrollment.json' and set(args)=={'data_b64','append'}
                        raw=base64.b64decode(args['data_b64'],validate=True);assert 0<len(raw)<=65536
                        storage[name]=(storage.get(name,b'') if args['append'] else b'')+raw
                        value={'status':'succeeded','operation_result':{**target,'bytes':len(raw),'size':len(storage[name]),'appended':args['append']}}
                    elif op=='fs.publish':
                        assert name=='uid/enrollment.json' and set(args) in ({'from','sha256','expected_absent'},{'from','sha256','expected_sha256'})
                        before=storage.get(name);new=storage[args['from']]
                        assert digest(new)==args['sha256'],'incomplete upload offered'
                        mismatch=conflict[0] or (before is not None if args.get('expected_absent') else before is None or digest(before)!=args['expected_sha256'])
                        if mismatch:value={'status':'failed','reasonCode':'FS_GENERATION_MISMATCH'}
                        else:
                            storage[name]=new;del storage[args['from']]
                            value={'status':'succeeded','operation_result':{**target,'size':len(new),'sha256':digest(new),'replaced':before is not None,'durable':durable[0],'durability':'synced' if durable[0] else 'unknown'}}
                    else:raise AssertionError(op)
                    calls.append({'operation':op,'target':target,'arguments_keys':sorted(args),'status':value['status'],'sha256':value.get('operation_result',{}).get('sha256')})
                    raw=json.dumps({'jsonrpc':'2.0','id':q['id'],'result':value}).encode()
                    with host.write_lock:host.process.stdin.write(struct.pack('>I',len(raw))+raw);host.process.stdin.flush()
            except Exception as e:broker_errors.append(repr(e))
        thread=threading.Thread(target=broker);thread.start()
        sid='enrollment-public-recordings'
        def send(op,args):
            host.counter+=1;key=host.counter
            # This fixture is the host: real operationTool.Execute always
            # stamps trusted invocation time, even on read-only speaker.list.
            # Act stamps below are fixture-host confirmations, not model input.
            args={**args,'_host_now_ms':time.time_ns()//1_000_000}
            message={'jsonrpc':'2.0','id':key,'method':'invoke.call','params':{'operation':op,'arguments':args}}
            raw=json.dumps(message).encode()
            with host.write_lock:host.process.stdin.write(struct.pack('>I',len(raw))+raw);host.process.stdin.flush()
            return key
        def wait(key,timeout=20,refused=False):
            value=host.responses.get(timeout=timeout);assert value['id']==key,value
            if refused:assert 'error' in value,value
            else:assert 'error' not in value,value
            r['cases'].append({'reply':value});save();return value.get('result')
        def call(op,args,**kw):return wait(send(op,args),**kw)
        def act(n):return {'id':'public-fixture-act-'+str(n),'confirmed_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}
        # Profile discovery is an ordinary plugin operation, not an audio
        # session. Prove it before any speech.session.open has occurred.
        closed=call('speaker.list',{})['operation_result']
        assert closed['session_open'] is False and closed['session_id']=='' and closed['eligible_final_sequences']==[]
        assert not storage and not any(e['type']=='session_start' for e in host.events)
        host.call('open',{'session_id':sid,'input_handle':'mic','output_handle':'speaker','audio':{'format':'s16le','input':{'rate':16000,'channels':1},'output':{'rate':24000,'channels':1}}})
        host.event('session_ready',session_id=sid)
        base={'session_id':sid,'speaker_id':'237','label':'Public evaluation speaker 237'}
        initial=call('speaker.list',{});assert initial['operation_result']['enrollment_file_absent'] and not storage
        assert initial['operation_result']['session_id']==sid and initial['operation_result']['eligible_final_sequences']==[]
        call('speaker.enroll',{**base,'finals':[1]},refused=True);assert not storage
        position=0;frame_seq=0;finals=[];source_pcm=bytearray();final_spans={}
        def recording(name):
            nonlocal position,frame_seq
            path=bind(artifacts/'deliverables/native-uid-cpp-20260911-r3/inputs'/name);raw=path.read_bytes()+bytes(16000*3*2)
            previous={e['sequence'] for e in host.events if e['type']=='transcript_final' and e['session_id']==sid}
            # Continuously open microphone stream. Silence—not a per-turn END—
            # asks the engine's actual VAD/endpoint policy to finalize each span.
            for offset in range(0,len(raw),1024):
                part=raw[offset:offset+1024];frame_seq+=1
                host.to_engine.write(Frame(PCM,9,frame_seq,position,part).encode());source_pcm.extend(part);position+=len(part)//2;time.sleep(.032)
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                new=[e for e in host.events if e['type']=='transcript_final' and e['session_id']==sid and e['sequence'] not in previous]
                if len(new)==1:
                    obs=[e for e in host.events if e['type']=='speaker_observation' and e['session_id']==sid and e['refers_to']==new[0]['sequence']]
                    if obs:
                        final_spans[new[0]['sequence']]=(new[0]['start_sample'],new[0]['end_sample'])
                        return new[0],obs[0]
                elif len(new)>1:raise AssertionError('fixture split into multiple final spans')
                time.sleep(.02)
            raise AssertionError('VAD-finalized recording/UID observation missing')
        for name in ('237-126133-0022.pcm','237-126133-0012.pcm','237-126133-0021.pcm'):
            final,obs=recording(name);assert obs['reason']=='enrollment_unavailable';finals.append(final['sequence'])
        print('three real VAD-finalized recordings retained',flush=True)
        discovered=call('speaker.list',{})['operation_result']
        assert discovered['session_id']==sid and discovered['eligible_final_sequences']==finals
        # The package, not this regression fixture, owns the recording policy.
        # Still enroll all three selected finals below; single-capture usability
        # is independently exercised by prove_guided_capture_sdk.
        expected_minimum=json.loads(policy.read_text())['minimum_enrollment_samples']
        assert expected_minimum in (1,3) and discovered['minimum_recordings_per_speaker']==expected_minimum
        r['bound_minimum_recordings_per_speaker']=expected_minimum
        call('speaker.enroll',{**base,'finals':[999999],'_host_operator_act':act(1)},refused=True)
        call('speaker.enroll',{**base,'finals':[finals[0],finals[0]],'_host_operator_act':act(2)},refused=True)
        assert 'uid/enrollment.json' not in storage
        # Hold the enrollment's host read while synthesis, stop and cancel use
        # the same actual SDK owner. Enrollment must not serialize interruption.
        entered.clear();hold.clear();key=send('speaker.enroll',{**base,'finals':finals,'_host_operator_act':act(3)})
        assert entered.wait(3)
        began=time.monotonic()
        host.call('synthesize',{'session_id':sid,'synthesis_id':'during-enrollment','text':'Enrollment storage cannot hold this reply hostage.'})
        host.call('stop_playback',{'session_id':sid,'synthesis_id':'during-enrollment'})
        host.call('cancel_synthesis',{'session_id':sid,'synthesis_id':'during-enrollment'})
        r['held_storage_control_seconds']=time.monotonic()-began;assert r['held_storage_control_seconds']<.75
        hold.set();enrolled=wait(key);assert enrolled['status']=='succeeded'
        person=enrolled['operation_result']['speakers'];assert len(person)==1 and person[0]['recordings']==3 and person[0]['ready']
        frozen=storage['uid/enrollment.json'];(a.out/'public-enrollment.snapshot.json').write_bytes(frozen)
        cancelled=host.event('synthesis_cancelled','during-enrollment',session_id=sid);receipt(host,sid,cancelled,stopped=True,retry=True)
        print('operator-confirmed enrollment published and read back; interruption stayed independent',flush=True)
        known,obs=recording('237-134500-0032.pcm');assert obs['decision']=='known' and obs['speaker']==base['label'],obs
        assert obs.get('speaker_id')==base['speaker_id'],'public UID lost stable enrolled ID'
        r['known_observation']=obs
        unknown,other=recording('672-122797-0069.pcm');assert other['decision']=='unknown' and not other['speaker'],other
        assert other.get('speaker_id')=='','unknown voice claimed a stable UID'
        r['unknown_observation']=other
        call('speaker.enroll',{**base,'finals':finals,'_host_operator_act':act(3)},refused=True)
        conflict[0]=True;call('speaker.remove',{'session_id':sid,'speaker_id':'237','_host_operator_act':act(4)},refused=True)
        assert storage['uid/enrollment.json']==frozen;conflict[0]=False
        durable[0]=False;unresolved=call('speaker.remove',{'session_id':sid,'speaker_id':'237','_host_operator_act':act(5)})
        assert unresolved['status']=='failed' and unresolved['operation_result']['publication']['readback_verified'] and not unresolved['operation_result']['speakers']
        durable[0]=True
        reset=call('speaker.reset',{'session_id':sid,'_host_operator_act':act(6)});assert reset['status']=='succeeded' and reset['operation_result']['revision']=='3'
        listed=call('speaker.list',{'session_id':sid});assert listed['operation_result']['speakers']==[] and not listed['operation_result']['enrollment_file_absent']
        host.call('synthesize',{'session_id':sid,'synthesis_id':'recovery','text':'Speaker enrollment is complete. This is the full recovery reply.'})
        end=host.event('synthesis_end','recovery',timeout=25,session_id=sid);receipt(host,sid,end,retry=True)
        host.call('finish_input',{'session_id':sid,'stream_id':'mic','end_sample':position});host.to_engine.write(Frame(END,9,frame_seq+1,position).encode())
        host.event('input_finished',session_id=sid);host.call('close',{'session_id':sid,'mode':'drain'});host.event('session_end',session_id=sid)
        closed=call('speaker.list',{})['operation_result']
        assert closed['session_open'] is False and closed['eligible_final_sequences']==[]
        assert not closed['speakers'] and closed['revision']=='3'
        removed=call('speaker.remove',{'speaker_id':'237','_host_operator_act':act(7)})['operation_result']
        assert removed['session_open'] is False and removed['revision']=='3'
        for expected_revision in ('4','5'):
            automatic=act(8);automatic['id']='auto'
            cleared=call('speaker.reset',{'_host_operator_act':automatic})['operation_result']
            assert cleared['session_open'] is False and cleared['revision']==expected_revision
            assert cleared['publication']['readback_verified'] and cleared['publication']['durable']
        r['closed_microphone_management_passed']=True
        r['standing_operator_confirmation_repeat_passed']=True
        # Reuse the complete recorded-speech barge-in/28-word/recovery gate on
        # the same resident process after the enrollment lifecycle. Its input
        # owns interruption; explicit stop alone cannot prove spoken barge-in.
        cfg.output=a.out/'spoken-regression';cfg.spoken_interrupt=True;cfg.playback_reports=True
        assert run(cfg,host=host,session_id='after-enrollment-spoken-regression',synthesis_prefix='reg-',close_host=False)
        regression=json.loads((cfg.output/'report.json').read_text())
        assert regression['passed'];r['spoken_regression_sha256']=sha(cfg.output/'report.json')
        r['spoken_interruption_opening_words_and_recovery_passed']=True
        r['events']=host.events;r['calls']=host.calls;r['broker_calls']=calls;r['finals_selected']=finals
        assert not broker_errors,broker_errors
        stop.set();thread.join(2);assert not thread.is_alive();r['exit_code']=host.close();host=None;assert r['exit_code']==0
        # Independent canonical codec and decision implementation, not just the
        # producer agreeing with its own bytes. Public fixture only.
        from runtime.speaker_identity.snapshot import load_snapshot,dump_snapshot
        from runtime.speaker_identity.identity import Policy
        with load_snapshot(frozen,Policy(**json.loads(policy.read_text()))) as decoded:
            assert dump_snapshot(decoded)==frozen
        expected={digest(source_pcm[start*2:end*2]) for start,end in (final_spans[n] for n in finals)}
        doc=json.loads(frozen);assert doc['revision']==1 and len(doc['speakers'])==1
        assert {s['audio_sha256'] for s in doc['speakers'][0]['samples']}==expected,'enrollment did not use exact selected final PCM'
        r['canonical_codec_verified']=True;r['selected_final_pcm_digests']=sorted(expected)
        r['public_enrollment_sha256']=digest(frozen)
        for name,h in r['bindings'].items():assert sha(name)==h,('changed binding',name)
        if not candidate:
            if checkpoint:assert verify_checkpoint(checkpoint)==(checkpoint_frozen,built,pin,bindings)
            elif prepared:assert verify_build(landed_build_root)==built and verify_sdk()[0]==pin
            else:assert verify_build(a.carrier_build)==built
            r['release_dependency_qualified']=True
        r['passed']=True;print(json.dumps({'passed':True,'known':obs['decision'],'unknown':other['decision'],'control_seconds':r['held_storage_control_seconds']}))
    except Exception as exc:
        import traceback
        r['error']=str(exc)
        r['traceback']=traceback.format_exc()
        raise
    finally:
        hold.set();stop.set()
        if host:
            r['cleanup_exit']=host.close()
            r['events']=host.events;r['calls']=host.calls
        r['broker_errors']=broker_errors;r['broker_calls']=calls;save()
if __name__=='__main__':main()
