"""Assemble a private, unsigned native test package using the pinned public SDK.

The .invalid URLs intentionally require operator-managed preseeding. This is
not catalog publication, a T3 signature, an installed gate or a mobile release.
"""
import argparse,hashlib,json,os,shutil,subprocess,tarfile
from pathlib import Path
from scripts.build_plugin_carrier import SDK_SOURCE,PIN
from scripts.package_native_runtime import verify
ROOT=Path(__file__).resolve().parents[1];GO='/usr/local/go1.27/bin/go'
SPEAKER_OPERATIONS={'speaker.enroll','speaker.list','speaker.remove','speaker.reset',
                    'speaker.discard_capture','speaker.upgrade_policy'}

def enrollment_interfaces(descriptors):
    """Current enrollment surface must ship whole, never a silently reduced kit."""
    ids=[d['id'] for d in descriptors]
    if len(ids)!=len(set(ids)):raise ValueError('duplicate operation descriptor')
    speech=[n for n in ids if n.startswith('speech.session.')]
    speaker=[n for n in ids if n.startswith('speaker.')]
    if len(speech)!=8 or set(speaker)!=SPEAKER_OPERATIONS or len(ids)!=14:
        raise ValueError('incomplete guided enrollment interface')
    for descriptor in descriptors:
        if descriptor['id'] in SPEAKER_OPERATIONS:
            if descriptor.get('operator_confirms',False)!=(descriptor['id']!='speaker.list'):
                raise ValueError('speaker confirmation declaration differs')
    return [{'id':'speech.session','version':1,'methods':speech},
            {'id':'speaker.uid','version':1,'methods':speaker}]

def platform_spec(profile):
    key=(profile['platform'],profile['arch'])
    options={('darwin','arm64'):('macos','arm64','cpu',['onnxruntime','ggml','Accelerate']),
             ('linux','amd64'):('linux','x86_64','vulkan',['onnxruntime','ggml','Vulkan','libgomp1']),
             ('windows','amd64'):('windows','x86_64','vulkan',['onnxruntime','ggml','Vulkan','LibTorch'])}
    if key not in options:raise ValueError('unsupported native package platform')
    platform,arch,backend,libraries=options[key]
    return {'platform':platform,'arch':arch,'backend':backend,'libraries':libraries,
            'variant':platform+'-'+arch+'-native','suffix':'.exe' if platform=='windows' else ''}
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def runtime_archive_executable(row,platform):
    # Go's Windows FileInfo has no POSIX execute bits on regular files. The
    # unchanged SDK derives inventory modes from that API, and the host checks
    # the same API after extraction. Python's .exe stat inference is different.
    return platform!='windows' and row['executable']
def save(p,v):
    with Path(p).open('x') as f:json.dump(v,f,indent=2,allow_nan=False)
def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--proof',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--enrollment',action='store_true',help='Require the full zero-argument enrollment proof and declare both supported interfaces')
    p.add_argument('--enrollment-proof',type=Path,help='Explicit successful rerun; earlier failed enrollment evidence stays intact')
    p.add_argument('--version',help='Explicit version for a fresh candidate; prior signed artifact versions remain untouched')
    p.add_argument('--go',type=Path,default=Path(GO))
    p.add_argument('--go-modcache',type=Path,help='Private offline build cache, excluded from runtime and bundle')
    p.add_argument('--schema-root',type=Path,help='Explicit package-only schema resources; does not alter the carrier source record')
    p.add_argument('--max-compressed-bytes',default='128M',help='Build-time archive budget, not a change to host operator ceilings')
    a=p.parse_args()
    a.checkpoint=a.checkpoint.resolve();a.proof=a.proof.resolve();a.out=a.out.resolve();a.out.mkdir(parents=True,exist_ok=False)
    frozen=json.loads((a.checkpoint/'freeze.json').read_text());assert frozen['passed']
    runtime=a.checkpoint/'runtime';profile=verify(runtime,frozen['runtime_manifest_sha256'])
    target=platform_spec(profile);carrier=runtime/('aii-voice-t3'+target['suffix'])
    assert not a.enrollment_proof or a.enrollment,'an enrollment proof requires enrollment mode'
    uid_dir=a.enrollment_proof.resolve() if a.enrollment_proof else a.proof/('enrollment' if a.enrollment else 'uid')
    settings_dir=a.proof/('memory/speech' if a.enrollment else 'settings')
    uid=json.loads((uid_dir/'result.json').read_text());settings=json.loads((settings_dir/'result.json').read_text());memory=json.loads((a.proof/'memory/result.json').read_text())
    for q in (uid,settings):assert q['passed'] and q['exit_code']==0 and q['checkpoint']['runtime_manifest_sha256']==frozen['runtime_manifest_sha256']
    if a.enrollment:
        from scripts.audit_native_enrollment_desktops import analyze
        from scripts.native_checkpoint_binding import verify_checkpoint
        verify_checkpoint(a.checkpoint)
        analyze(lambda name:(uid_dir/name).read_bytes(),'',uid)
        assert not uid['sdk_candidate_only'] and uid['sdk_revision']==PIN['revision']
        assert uid['launch_command']==[str(carrier)]
    else:assert [c['name'] for c in uid['cases']]==['good','refused','known_while_host_read_delayed']
    assert len(settings['cases'])==13
    assert memory['passed'] and not memory['survivors'] and memory['freeze_sha256']==sha(a.checkpoint/'freeze.json')
    env={**os.environ,'GOTOOLCHAIN':'local','GOWORK':'off','GOPROXY':'off','CGO_ENABLED':'0'}
    if a.go_modcache:env.update(GOMODCACHE=str(a.go_modcache.resolve()),GOSUMDB='off')
    def run(name,command,limit=180):
        done=subprocess.run(list(map(str,command)),cwd=SDK_SOURCE,env=env,capture_output=True,timeout=limit)
        (a.out/(name+'.stdout')).write_bytes(done.stdout);(a.out/(name+'.stderr')).write_bytes(done.stderr);assert done.returncode==0,(name,done.returncode)
        return done.stdout
    sdk=a.out/('aiisdk'+target['suffix']);assembler=a.out/('assemble'+target['suffix'])
    run('sdk-build',[a.go,'build','-trimpath','-buildvcs=false','-o',sdk,'./cmd/aiisdk'])
    rows={**profile['files'],'voice-runtime.json':{'sha256':frozen['runtime_manifest_sha256'],'bytes':(runtime/'voice-runtime.json').stat().st_size,'executable':False}}
    tree=a.out/'companion-tree';tree.mkdir()
    for name,row in rows.items():
        dest=tree/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(runtime/name,dest);dest.chmod(0o755 if row['executable'] else 0o644);assert sha(dest)==row['sha256']
    archive=a.out/(target['variant']+'-runtime.tar.gz')
    declaration=json.loads(run('runtime-pack',[sdk,'runtime-pack','-dir',tree,'-o',archive,'-root','runtime','-max-installed-bytes',sum(v['bytes'] for v in rows.values()),'-max-files',len(rows),'-max-file-bytes',max(v['bytes'] for v in rows.values()),'-max-compressed-bytes',a.max_compressed_bytes,'-max-depth','8']))
    assert sha(archive)==declaration['sha256'] and archive.stat().st_size==declaration['size']
    with tarfile.open(archive,'r:gz') as tar:
        files=[m for m in tar if m.isfile()];assert len(files)==len(rows)+1 and len({m.name for m in files})==len(files)
        inventory=next(m for m in files if m.name=='runtime/inventory.json');raw=tar.extractfile(inventory).read();assert hashlib.sha256(raw).hexdigest()==declaration['inventory_sha256']
        seen={}
        for m in files:
            if m==inventory:continue
            assert m.name.startswith('runtime/');name=m.name[8:];row=rows[name]
            executable=runtime_archive_executable(row,target['platform'])
            assert m.size==row['bytes'] and m.mode==(0o755 if executable else 0o644) and hashlib.file_digest(tar.extractfile(m),'sha256').hexdigest()==row['sha256']
            seen[name]={'path':name,'size':m.size,'sha256':'sha256:'+row['sha256'],'mode':'exec' if executable else 'file'}
        assert set(seen)==set(rows) and json.loads(raw)['files']==[seen[n] for n in sorted(seen)]
    models=[{'name':'native-'+hashlib.sha256(n.encode()).hexdigest()[:20],'path':n,'url':'https://checkpoint.invalid/native-models/'+n,'sha256':v['sha256'],'size':v['bytes']} for n,v in sorted(frozen['models'].items())]
    variant=target['variant'];decls=json.loads((a.checkpoint/'settings.json').read_text());assert len(decls)==7
    config={'id':'id.aiii.voice','version':'0.1.0-native-cp1','plugin_family':'voice_interface','title':'AII Voice native English checkpoint','description':'Private native checkpoint: ten stable voices, configurable pause/VAD, STT/TTS and read-only enrolled-speaker observations. English only. Enrollment editing, echo and physical qualification incomplete. Not an upgrade to the multilingual Mac checkpoint.','interface':{'id':'speech.session','version':1},'capability_envelope':['fs.private'],
      'variants':[{'variant_id':variant,'platform':target['platform'],'arch':target['arch'],'topology':'full_identity_host','execution_runtime':'native_t3_component','admission_profile':'platform_reserved','variant_capabilities':['fs.private'],'accelerator':{'os':target['platform'],'arch':target['arch'],'backend':target['backend'],'precision':'mixed','models':[m['name'] for m in models],'memory_bytes':memory['sampled_peak_sum_rss_bytes'],'session_limit':1,'fallback':'none','runtime_libraries':target['libraries'],'operators':['streaming-asr','pocket-tts','silero-vad','smart-turn','wespeaker']}}],
      'settings':decls,'models':models,'runtimes':[{'variant_id':variant,'url':'https://checkpoint.invalid/runtime/'+variant+'-cp1.tar.gz',**{k:declaration[k] for k in ('sha256','size','installed_bytes','files','inventory_sha256')}}]}
    shutil.copyfile(carrier,a.out/'aii-voice-t3');(a.out/'aii-voice-t3').chmod(0o755)
    # Execute the platform-named original. The assembler's input basename is
    # intentionally common; its emitted entrypoint comes from the variant.
    desc=subprocess.run([str(carrier)],env={'PATH':'','AIISDK_DESCRIBE':'1'},capture_output=True,check=True,timeout=10)
    descriptors=json.loads(desc.stdout);assert not desc.stderr and len(descriptors)==(14 if a.enrollment else 8)
    (a.out/'descriptors.json').write_bytes(desc.stdout)
    schema_files={}
    schema_root=a.schema_root.resolve() if a.schema_root else ROOT/'plugin/native'
    for d in descriptors:
        for ref in (d.get('input'),d.get('output')):
            if ref:
                relative=Path(ref)
                assert not relative.is_absolute() and '..' not in relative.parts and ref.startswith('schemas/')
                target_schema=a.out/relative;target_schema.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(schema_root/relative,target_schema)
                schema_files[ref]=sha(target_schema)
    if a.enrollment:
        del config['interface']
        interfaces=enrollment_interfaces(descriptors)
        config.update(version='0.1.0-native-cp3-enrollment1',title='AII Voice CP3 — enrollment test candidate',
          description='Private native candidate: ten stable speaking voices, English STT/TTS, active VAD with adjustable pause, and operator-confirmed speaker enrollment. Speaker identification grants no command authority. Installed-host isolation, echo, mobile and human-level qualification remain incomplete.',
          interfaces=interfaces)
    if a.version:config['version']=a.version
    save(a.out/'plugin.json',config)
    run('assemble-build',[a.go,'build','-trimpath','-buildvcs=false','-o',assembler,ROOT/'scripts/private_cp1_package.go'])
    assembly=json.loads(run('assemble',[assembler,a.out],30));bundle=a.out/assembly['bundle'];assert sha(bundle)==assembly['sha256']
    expected={f.relative_to(a.out/'stage').as_posix():f for f in (a.out/'stage').rglob('*') if f.is_file()}
    with tarfile.open(bundle) as tar:
        members=[m for m in tar if m.isfile()];assert len(members)==len(expected) and {m.name for m in members}==set(expected)
        for m in members:assert tar.extractfile(m).read()==expected[m.name].read_bytes()
    verify(runtime,frozen['runtime_manifest_sha256'])
    result={'passed':True,'signed':False,'installed':False,'published':False,'human_level_qualified':False,'ready_for_private_test_signing_review':True,'sdk_revision':PIN['revision'],'bundle':assembly,'operation_schema_files':schema_files,'runtime_archive':{'path':str(archive),**declaration},'models_root':frozen['models_root'],'model_files':len(models),'runtime_manifest_sha256':frozen['runtime_manifest_sha256'],'settings_count':7,'sampled_memory_lower_bound_bytes':memory['sampled_peak_sum_rss_bytes'],'checkpoint_freeze_sha256':sha(a.checkpoint/'freeze.json'),'uid_read_observation_proof_sha256':sha(uid_dir/'result.json'),'settings_proof_sha256':sha(settings_dir/'result.json'),'memory_proof_sha256':sha(a.proof/'memory/result.json'),'limits':['English-only native profile','CPU inference on Mac checkpoint','UID enrollment editing not implemented here','public fixture UID proof is not operator identification','simulated broker/sink, not installed browser','echo and mobile whole-session qualification incomplete','requires operator T3 signing and dependency preseeding; no downloads from placeholder URLs']}
    if a.enrollment:
        result.update(ready_for_private_test_signing_review=False,uid_proof_kind='recorded-final enrollment, CAS/readback, identification and spoken recovery',
          host_installation_blocker='Invocation-owned broker authorization/resource isolation must replace the proposed removal of per-operation restrictions')
        result['limits']=[x for x in result['limits'] if x!='UID enrollment editing not implemented here']
        result['limits'].append('Enrollment operations tested through simulated broker; no installed operator enrollment proof')
    result['platform']=target['platform']
    if target['platform']!='macos':
        result['limits']=[x for x in result['limits'] if x!='CPU inference on Mac checkpoint']
        result['limits'].append('Vulkan TTS; CPU ASR, VAD, endpoint and UID')
    result['limits'].append('Memory declaration is observed process RSS, not a worst-case RAM or VRAM guarantee')
    save(a.out/'handoff.json',result);print(json.dumps({'passed':True,'bundle':assembly,'models':len(models),'runtime_bytes':declaration['installed_bytes'],'signed':False}),flush=True)
if __name__=='__main__':main()
