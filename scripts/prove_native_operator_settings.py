"""Actual native SDK voice/settings selection; recorded PCM, simulated sink.

No signing, installation, microphone capture or human-quality claim.
"""
import argparse,hashlib,json,os,shutil,time,wave
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from scripts.prove_plugin_sdk_engine import SDKHost
from scripts.plugin_receipt_probe import receipt
from scripts.build_plugin_carrier import BUILD_DIR,SDK_SOURCE,verify_build
from runtime.plugin_engine.audio import PCM
ROOT=Path(__file__).resolve().parents[1]
def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--build',type=Path,required=True);p.add_argument('--checkpoint',type=Path);p.add_argument('--prepared',type=Path)
    p.add_argument('--carrier-build',type=Path,help='Explicit source-verified development carrier; frozen profiles own their carrier')
    p.add_argument('--artifact-root', type=Path, required=True)
    a=p.parse_args()
    artifacts=a.artifact_root.resolve()
    a.out=a.out.resolve();a.build=a.build.resolve();a.out.mkdir(parents=True,exist_ok=False)
    # Keep the exact executed test, not only its digest, as the driver evolves.
    shutil.copyfile(Path(__file__),a.out/'executed-harness.py')
    r={'passed':False,'scope':__doc__,'bindings':{},'cases':[]};host=None
    def bind(p):p=Path(p);r['bindings'][str(p)]=sha(p);return p
    def save():(a.out/'result.json').write_text(json.dumps(r,indent=2)+'\n')
    try:
        prepared=json.loads(a.prepared.read_text()) if a.prepared else None
        checkpoint=a.checkpoint.resolve() if a.checkpoint else None
        assert not (checkpoint and prepared),'one execution profile required'
        if a.carrier_build and (checkpoint or prepared):
            raise ValueError('carrier selection belongs to the checkpoint or prepared profile')
        # A frozen checkpoint owns its carrier/source binding. A stale,
        # unrelated development build must neither approve nor block it.
        if not checkpoint:
            build=ROOT/'.build/native-sdk-5be6855-uid-r1' if prepared else (a.carrier_build.resolve() if a.carrier_build else BUILD_DIR)
            cr=json.loads((build/'build.json').read_text()) if prepared else verify_build(build)
            r['sdk_revision']=cr['sdk_revision'];bind(build/'build.json')
        for p in (ROOT/'runtime/native/session').glob('*'):
            if p.is_file():bind(p)
        bind(ROOT/'runtime/native_pocket/resident.cpp');bind(Path(__file__))
        if checkpoint:
            from scripts.native_checkpoint_binding import verify_checkpoint
            checkpoint_binding=verify_checkpoint(checkpoint)
            frozen,_,_,bindings=checkpoint_binding
            r['sdk_revision']=checkpoint_binding[1]['sdk_revision']
            for name,h in bindings.items():assert sha(bind(name))==h
            platform=frozen.get('platform','macos');windows=platform=='windows'
            carrier=checkpoint/'runtime'/('aii-voice-t3.exe' if windows else 'aii-voice-t3')
            binary=checkpoint/'runtime/bin'/('aii_voice_worker.exe' if windows else 'aii_voice_worker')
            bank=Path(frozen['models_root'])/'tts/embeddings';backend=frozen.get('backend','cpu');command=None
            voices=sorted(bank.glob('*.safetensors'));assert len(voices)==10
            for file in voices:bind(file)
        elif prepared:
            assert prepared['passed'];bind(a.prepared)
            for name,digest in prepared['bindings'].items():assert sha(name)==digest;bind(name)
            core={'command':[prepared['worker'],*prepared['model_paths']]};bank=Path(prepared['voice_bank']);backend=prepared['backend'];carrier=Path(prepared['carrier']);binary=Path(prepared['worker'])
            assert sha(carrier)==cr['artifacts'][carrier.name]['sha256'];bind(carrier)
        else:
            core=json.loads(bind(artifacts/'deliverables/native-c-embedding-20260912-r2/result.json').read_text());assert core['passed']
            for n,row in core['models'].items():assert sha(n)==row['sha256'];bind(n)
            bank=artifacts/'deliverables/operator-voice-catalog-20260911-r1/embeddings';backend='cpu';carrier=build/'aii-voice-t3-race';binary=a.build/'aii_voice_worker'
            bind(a.build/'libaii_voice_runtime.dylib');bind(a.build/'CMakeCache.txt')
        if not checkpoint:
            assets=a.out/'assets';assets.mkdir();(assets/'embeddings').mkdir()
            original=Path(core['command'][6])
        def link(source,dest):
            bind(source)
            try:os.link(source,dest)
            except OSError:shutil.copyfile(source,dest)
            assert sha(dest)==sha(source),'model transfer changed bytes'
        for name in (() if checkpoint else ('model.safetensors','tokenizer.model')):link(original/name,assets/name)
        # The prepared profile owns the config path separately from the model
        # directory (the Windows retained checkpoint intentionally does so).
        if not checkpoint:
            link(Path(core['command'][7]),assets/'config.yaml')
            voices=sorted(bank.glob('*.safetensors'));assert len(voices)==10
            for file in voices:link(file,assets/'embeddings'/file.name)
            command=[str(bind(binary)),*core['command'][1:6],str(assets),str(assets/'config.yaml'),backend]
            if prepared:command += [prepared['uid_model'],prepared['policy']]
        cfg=SimpleNamespace(output=a.out/'owner',carrier=carrier,fixture=False,backend='native-common-'+backend,stage=None,operator_settings={},spoken_interrupt=True,playback_reports=True,sdk_source=SDK_SOURCE)
        if checkpoint:
            from scripts.native_checkpoint_binding import verify_checkpoint
            checkpoint_binding=verify_checkpoint(checkpoint)
            for name,h in checkpoint_binding[3].items():assert sha(bind(name))==h
            from scripts.package_native_runtime import verify
            frozen=json.loads(bind(checkpoint/'freeze.json').read_text());assert frozen['passed']
            profile=verify(checkpoint/'runtime',frozen['runtime_manifest_sha256'])
            for name in profile['files']:bind(checkpoint/'runtime'/name)
            for name,row in frozen['models'].items():assert sha(Path(frozen['models_root'])/name)==row['sha256'];bind(Path(frozen['models_root'])/name)
            cfg.packaged_runtime=True;cfg.runtime_manifest_sha=frozen['runtime_manifest_sha256'];cfg.model_data_root=Path(frozen['models_root']);cfg.carrier=bind(carrier)
            assert sha(cfg.carrier)==frozen['carrier_sha256']
            r['checkpoint']={'root':str(checkpoint),'runtime_manifest_sha256':frozen['runtime_manifest_sha256']}
        cfg.output.mkdir();host=SDKHost(cfg,worker_command=None if checkpoint else command);r['ready']=host.readiness();r['ready_observed_monotonic_ns']=time.monotonic_ns();r['command']=host.launch_command;assert r['ready']['models_loaded']==(5 if checkpoint or prepared else 4)
        if prepared:
            from scripts.native_loaded_images import observe
            r['loaded_worker']=observe(host.process.pid,binary.parent,prepared['platform'],prepared['libraries'])
        elif checkpoint and platform in ('linux','windows'):
            from scripts.native_loaded_images import observe
            r['loaded_worker']=observe(host.process.pid,binary.parent,platform,frozen['library_hashes'])
        text='Hello. I will keep this voice throughout our conversation.'
        base={'tts_voice':'alba','tts_language':'en','stt_language':'en','turn_pause_ms':768,'vad_threshold':.5,'tts_temperature':.3,'tts_seed':20260908,'capture_limit_minutes':30}
        cases=[(file.stem,{**base,'tts_voice':file.stem,'turn_pause_ms':1200,'vad_threshold':.65}) for file in voices]
        cases += [('alba-repeat',{**base,'turn_pause_ms':1200,'vad_threshold':.65}),('alba-new-seed',{**base,'tts_seed':7}),('alba-new-temperature',{**base,'tts_temperature':.7})]
        outputs={}
        for index,(name,settings) in enumerate(cases,1):
            host.operator_settings=settings;sid='settings-'+str(index)
            host.call('open',{'session_id':sid,'input_handle':'mic','output_handle':'speaker','audio':{'format':'s16le','input':{'rate':16000,'channels':1},'output':{'rate':24000,'channels':1}}});host.event('session_ready',session_id=sid)
            status=host.call('status',{'session_id':sid});effective=status['operator_settings']
            assert set(effective)==set(settings)
            for k,v in settings.items():assert (abs(effective[k]-v)<1e-6 if type(v) is float else effective[k]==v),(k,v,effective[k])
            start=time.perf_counter();synth='voice-'+str(index);host.call('synthesize',{'session_id':sid,'synthesis_id':synth,'text':text})
            first=host.first_pcm(host.event('synthesis_start',synth,session_id=sid)['output_stream'])
            end=host.event('synthesis_end',synth,timeout=60,session_id=sid);seconds=time.perf_counter()-start
            receipt(host,sid,end)
            payload=b''.join(f.pcm for _,f in host.frames if f.stream==end['output_stream'] and f.kind==PCM)
            samples=np.frombuffer(payload,dtype='<i2');assert len(samples)==end['delivered_samples'] and len(samples)>24000 and np.max(np.abs(samples.astype(np.int32)))>100
            output=a.out/(name+'.wav')
            with wave.open(str(output),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(payload)
            outputs[name]=hashlib.sha256(payload).hexdigest()
            host.call('finish_input',{'session_id':sid,'stream_id':'mic','end_sample':0});host.event('input_finished',session_id=sid)
            host.call('close',{'session_id':sid,'mode':'drain'});host.event('session_end',session_id=sid)
            r['cases'].append({'name':name,'effective':effective,'model_execution':status.get('model_execution'), 'pcm_sha256':outputs[name],'wav_sha256':sha(output),'samples':len(samples),'seconds':seconds,'rtf':seconds/(len(samples)/24000),'first_pcm':first});save()
        assert outputs['alba']==outputs['alba-repeat'],'voice/seed not stable across sessions'
        assert outputs['alba']!=outputs['alba-new-seed'],'seed accepted but inert'
        assert outputs['alba']!=outputs['alba-new-temperature'],'temperature accepted but inert'
        assert len({outputs[p.stem] for p in voices})==10,'voice choice did not change PCM'
        r['speech_complete_monotonic_ns']=time.monotonic_ns()
        r['events']=host.events;r['calls']=host.calls;r['exit_code']=host.close();host=None;assert r['exit_code']==0
        for n,d in r['bindings'].items():assert sha(n)==d,'input changed '+n
        if checkpoint:assert verify_checkpoint(checkpoint)==checkpoint_binding
        elif not prepared:assert verify_build(build)==cr
        r['passed']=True;save();print(json.dumps({'passed':True,'voices':10,'cases':len(cases),'ready':r['ready']}),flush=True)
    finally:
        if host:r['cleanup_exit']=host.close()
        save()
if __name__=='__main__':main()
