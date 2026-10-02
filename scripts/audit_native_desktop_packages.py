"""Read back a desktop CP3 bundle, companion bytes, and raw measured evidence.

This is private artifact qualification, not signing, installed-host admission,
worst-case memory, GPU memory, or human-level quality qualification.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import tarfile

from scripts.audit_native_enrollment_desktops import archive, digest, analyze
from scripts.audit_packaged_native_desktops import bound_result, settings_evidence


def normalize(path):
    return path.replace('\\', '/').removeprefix('//?/')


def carrier_refresh_evidence(frozen, parent, parent_raw, profile, parent_profile, settings_raw, parent_settings_raw):
    assert frozen['parent_freeze_sha256']==digest(parent_raw),'wrong parent freeze'
    assert profile==parent_profile,'carrier refresh changed native runtime'
    assert frozen['runtime_manifest_sha256']==parent['runtime_manifest_sha256']
    assert frozen['models']==parent['models'],'carrier refresh changed model binding'
    assert frozen['models_root']==parent['models_root'],'carrier refresh moved model authority'
    assert frozen['library_hashes']==parent['library_hashes']
    # Earlier desktop freezes bound settings by file, without Mac's summary
    # hash field. Compare the archived bytes, not an assumed summary schema.
    assert settings_raw==parent_settings_raw,'carrier refresh changed settings bytes'
    assert frozen['settings_sha256']==digest(settings_raw)
    assert frozen['carrier_sha256']!=parent['carrier_sha256'],'carrier was not refreshed'


def memory_evidence(memory, speech, frozen, freeze_raw, retired, windows=False):
    assert memory['passed'] and memory['exit_code']==0 and not memory['survivors']
    assert memory['freeze_sha256']==digest(freeze_raw)
    assert memory['carrier_sha256']==frozen['carrier_sha256']
    assert memory['owner_pid'] in retired
    loaded=speech['loaded_worker'];root=normalize(speech['checkpoint']['root'])+'/runtime/'
    suffix='.exe' if windows else ''
    wanted={loaded['pid']:root+'bin/aii_voice_worker'+suffix,
            loaded['parent_pid']:root+'aii-voice-t3'+suffix}
    extras=memory.get('pre_resident_carrier_pids',[])
    assert len(extras)==len(set(extras)) and not set(extras)&set(wanted)
    wanted.update({pid:root+'aii-voice-t3'+suffix for pid in extras})
    first={};last={}
    seen={};samples=memory['samples'];assert len(samples)>1
    assert len(samples)==memory['sample_count']
    for i,sample in enumerate(samples):
        assert len({p['pid'] for p in sample['processes']})==len(sample['processes'])
        if i:
            assert sample['elapsed']>samples[i-1]['elapsed']
            assert sample['observed_monotonic_ns']>samples[i-1]['observed_monotonic_ns']
        for row in sample['processes']:
            pid=row['pid'];assert pid in wanted and pid in retired
            for key in ('exe','observed_exe'):
                actual,expected=normalize(row[key]),wanted[pid]
                if windows:actual,expected=actual.casefold(),expected.casefold()
                assert actual==expected,('wrong executable',row[key])
            assert row['rss']>0 and row['created']>0
            if pid in seen:assert seen[pid]==row['created'],'reused process id'
            seen[pid]=row['created']
            first.setdefault(pid,sample['observed_monotonic_ns']);last[pid]=sample['observed_monotonic_ns']
            if pid in extras:
                assert row['parent_pid']==memory['owner_pid']
    assert set(seen)==set(wanted)
    for pid in extras:
        assert seen[pid]<seen[loaded['parent_pid']]
        assert last[pid]<first[loaded['parent_pid']]
    peak=max(sum(p['rss'] for p in row['processes']) for row in samples)
    gap=max(b['elapsed']-a['elapsed'] for a,b in zip(samples,samples[1:]))
    assert peak==memory['sampled_peak_sum_rss_bytes']
    assert gap==memory['max_sample_gap_seconds']
    return {'sampled_peak_sum_rss_bytes':peak,'samples':len(samples),'max_gap_seconds':gap,
            'process_births':seen,'pre_resident_carrier_pids':extras,
            'worst_case_memory_qualified':False,'gpu_memory_measured':False}


def tar_files(path):
    result={};modes={};seen=set()
    with tarfile.open(path) as tar:
        for member in tar:
            name=PurePosixPath(member.name)
            assert not name.is_absolute() and '..' not in name.parts
            assert member.name not in seen,'duplicate tar member';seen.add(member.name)
            if member.isdir():continue
            assert member.isfile(),'non-regular archive member'
            raw=tar.extractfile(member).read();assert len(raw)==member.size
            result[member.name]=raw;modes[member.name]=member.mode
    return result,modes


def assert_binary_target(raw, platform):
    if platform=='linux':
        assert raw[:6]==b'\x7fELF\x02\x01' and struct.unpack_from('<H',raw,18)[0]==62
    else:
        assert raw[:2]==b'MZ';offset=struct.unpack_from('<I',raw,0x3c)[0]
        assert raw[offset:offset+4]==b'PE\0\0' and struct.unpack_from('<H',raw,offset+4)[0]==0x8664

def operation_schemas(install,descriptors):
    refs={d[k] for d in descriptors for k in ('input','output') if d.get(k)}
    assert len(refs)==5,'expected four enrollment inputs and one output'
    for ref in refs:
        name=PurePosixPath(ref)
        assert ref.startswith('schemas/') and not name.is_absolute() and '..' not in name.parts and '\\' not in ref
        assert ref in install,('missing declared schema',ref)
        assert isinstance(json.loads(install[ref]),dict)
    return {ref:digest(install[ref]) for ref in sorted(refs)}


def package_evidence(artifacts,handoff,frozen,profile,settings,descriptors,platform):
    bundle=artifacts/handoff['bundle']['bundle']
    raw=bundle.read_bytes();assert digest(raw)==handoff['bundle']['sha256']
    assert len(raw)==handoff['bundle']['bytes']
    members,modes=tar_files(bundle)
    prefix=bundle.name.removesuffix('.aiiospkg')+'/'
    manifest=json.loads(members[prefix+'manifest.json'])
    install={n.removeprefix(prefix+'install-root/'):b for n,b in members.items() if n.startswith(prefix+'install-root/')}
    assert set(members)=={prefix+'manifest.json'}|{prefix+'install-root/'+n for n in install}
    aggregate=hashlib.sha256()
    for name,body in sorted(install.items()):aggregate.update((name+'\0'+digest(body)+'\n').encode())
    assert 'sha256:'+aggregate.hexdigest()==manifest['package_hash']==handoff['bundle']['package_hash']
    stripped={k:v for k,v in manifest.items() if k!='package_hash'}
    canonical=json.dumps(stripped,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    assert 'sha256:'+digest(canonical)==handoff['bundle']['manifest_hash']
    assert manifest['capability_envelope']==['fs.private']
    assert len(manifest['variants'])==1
    variant=manifest['variants'][0];variant_id=platform+'-x86_64-native'
    assert (variant['platform'],variant['arch'],variant['variant_id'])==(platform,'x86_64',variant_id)
    assert variant['variant_capabilities']==['fs.private']
    assert variant['execution_runtime']=='native_t3_component'
    carrier=install[variant['entrypoint']]
    assert digest(carrier)==frozen['carrier_sha256']==variant['artifact_hash'].removeprefix('sha256:')
    assert_binary_target(carrier,platform)
    # The SDK's canonical bundle format stores all regular files as 0644.
    # Host activation applies the entrypoint's executable mode; the companion
    # inventory below independently retains its exec/file distinction.
    assert set(modes.values())=={0o644}
    allowed={'speaker.enroll','speaker.list','speaker.remove','speaker.reset',
             'speech.session.'+'open','speech.session.synthesize','speech.session.cancel_synthesis',
             'speech.session.stop_playback','speech.session.finish_input','speech.session.close',
             'speech.session.status','speech.session.playback_report'}
    assert len(descriptors)==12 and {d['id'] for d in descriptors}==allowed
    seen=set()
    for interface in manifest['interfaces']['core']:
        schema=install['interfaces/'+interface['id']+'.v1.schema.json']
        assert 'sha256:'+digest(schema)==interface['schema_hash']
        rows=json.loads(schema);assert [r['id'] for r in rows]==interface['methods']
        for row in rows:
            assert row['id'] not in seen;seen.add(row['id'])
            assert row==next(d for d in descriptors if d['id']==row['id'])
            if row['id'].startswith('speaker.'):
                assert row.get('operator_confirms',False)==(row['id']!='speaker.list')
    assert seen==allowed
    schemas=operation_schemas(install,descriptors)
    assert variant['implements']['core']==['speech.session@1','speaker.uid@1']
    assert json.loads(install['settings.json'])==settings and len(settings)==7
    models=json.loads(install['models.json'])
    assert len(models)==24 and len({m['path'] for m in models})==24
    assert {m['path']:(m['sha256'],m['size']) for m in models}=={n:(r['sha256'],r['bytes']) for n,r in frozen['models'].items()}
    declaration=json.loads(install['runtime.json'])['runtimes'];assert len(declaration)==1
    declaration=declaration[0];assert declaration['variant_id']==variant_id
    for key in ('sha256','size','files','installed_bytes','inventory_sha256'):
        assert declaration[key]==handoff['runtime_archive'][key]
    runtime=artifacts/(variant_id+'-runtime.tar.gz')
    assert digest(runtime.read_bytes())==declaration['sha256'] and runtime.stat().st_size==declaration['size']
    files,modes=tar_files(runtime);inventory=files['runtime/inventory.json']
    assert digest(inventory)==declaration['inventory_sha256']
    expected={**profile['files'],'voice-runtime.json':{'sha256':frozen['runtime_manifest_sha256'],'bytes':len(files['runtime/voice-runtime.json']),'executable':False}}
    assert json.loads(files['runtime/voice-runtime.json'])==profile
    assert set(files)=={'runtime/inventory.json'}|{'runtime/'+n for n in expected}
    rows=[]
    for name,row in sorted(expected.items()):
        body=files['runtime/'+name]
        assert len(body)==row['bytes'] and digest(body)==row['sha256']
        # Native Go Windows packing and host revalidation both see no POSIX
        # execute bits. This is not the engine's Python-inferred .exe flag.
        executable=row['executable'] and platform!='windows'
        mode='exec' if executable else 'file'
        assert modes['runtime/'+name]==(0o755 if executable else 0o644)
        rows.append({'path':name,'size':len(body),'sha256':'sha256:'+digest(body),'mode':mode})
    assert json.loads(inventory)['files']==rows
    # RuntimePack budgets and declarations count payloads, not the generated
    # inventory itself. The inventory is separately bound by its own digest.
    assert len(rows)==declaration['files'] and sum(r['size'] for r in rows)==declaration['installed_bytes']
    accelerator=json.loads(install['accelerator.json'])[variant_id]
    assert accelerator['os']==platform and accelerator['arch']=='x86_64' and accelerator['backend']=='vulkan'
    assert accelerator['session_limit']==1 and accelerator['fallback']=='none'
    assert accelerator['memory_bytes']==handoff['sampled_memory_lower_bound_bytes']
    assert accelerator['models']==[m['name'] for m in models]
    return {'bundle':str(bundle),'bundle_sha256':digest(raw),'bundle_bytes':len(raw),
            'runtime_archive':str(runtime),'runtime_sha256':declaration['sha256'],
            'runtime_archive_bytes':declaration['size'],'runtime_installed_bytes':declaration['installed_bytes'],
            'runtime_files':declaration['files'],'model_bytes':sum(r['bytes'] for r in frozen['models'].values()),
            'methods':sorted(seen),'operation_schemas':schemas,'signed':False,'installed':False}


def main():
    p=argparse.ArgumentParser()
    for name in ('source','sampler-source','baseline-evidence','evidence','artifacts','out'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--platform',choices=('linux','windows'),required=True)
    p.add_argument('--carrier-refresh',action='store_true',help='Require fresh carrier, enrollment and memory evidence while preserving parent runtime/model bytes')
    a=p.parse_args();assert not a.out.exists();prefix='run/' if a.platform=='windows' else ''
    source,files=archive(a.source);sampler,_=archive(a.sampler_source)
    pin=json.loads(source.read('plugin/sdk-source.json'))
    assert digest(source.read(pin['archive']))==pin['archive_sha256']
    base,_=archive(a.baseline_evidence);z,_=archive(a.evidence)
    get=lambda n:json.loads(z.read(prefix+n));old=lambda n:json.loads(base.read(prefix+n))
    terminal=get('complete.json');assert terminal['passed'] and terminal['platform']==a.platform
    assert not terminal['signed'] and not terminal['installed'] and not terminal['human_level_qualified']
    stages=get('post-preparation.json');assert stages['passed'] and 'active' not in stages
    names=[s['name'] for s in stages['stages']]
    if a.carrier_refresh:
        assert names==['carrier-tests','refresh','enrollment','memory','package']
        assert not stages.get('memory_reuse') and stages['all_thirteen_pcm_identical']
    else:assert names==(['package'] if stages.get('memory_reuse') else ['memory','package'])
    retired=set(json.loads(z.read('retirement.json'))['absent_pids'])
    assert all(s['exit_code']==0 and s['retired'] and s['pid'] in retired for s in stages['stages'])
    current=z if a.carrier_refresh else base
    bound=lambda name:json.loads(current.read(prefix+name))
    frozen=bound('checkpoint/freeze.json');freeze_raw=current.read(prefix+'checkpoint/freeze.json')
    build=bound('checkpoint/carrier-build.json')
    assert build['sdk_revision']==frozen['sdk_revision']==pin['revision']
    assert build['carrier_sha256']==frozen['carrier_sha256']
    for name,checksum in build['inputs'].items():assert files[name]['sha256']==checksum
    profile=bound('checkpoint/runtime/voice-runtime.json')
    assert digest(current.read(prefix+'checkpoint/runtime/voice-runtime.json'))==frozen['runtime_manifest_sha256']
    if a.carrier_refresh:
        carrier_refresh_evidence(frozen,old('checkpoint/freeze.json'),base.read(prefix+'checkpoint/freeze.json'),profile,old('checkpoint/runtime/voice-runtime.json'),current.read(prefix+'checkpoint/settings.json'),base.read(prefix+'checkpoint/settings.json'))
    memory=get('memory/result.json');speech=get('memory/speech/result.json')
    assert memory['recipe_sha256']==digest(sampler.read('scripts/measure_common_native_checkpoint.py'))
    assert memory['speech_sha256']==digest(z.read(prefix+'memory/speech/result.json'))
    if stages.get('memory_reuse'):assert stages['memory_reuse']['sha256']==digest(z.read(prefix+'memory/result.json'))
    carrier=normalize(speech['checkpoint']['root'])+'/runtime/aii-voice-t3'+('.exe' if a.platform=='windows' else '')
    actual_carrier=speech.get('launch_command',speech.get('command'))[0]
    assert normalize(actual_carrier)==carrier
    bound_result(speech,frozen,actual_carrier,retired)
    bindings={normalize(n):h for n,h in speech['bindings'].items()}
    root=normalize(speech['checkpoint']['root'])+'/runtime/'
    for n,r in profile['files'].items():assert bindings[root+n]==r['sha256']
    for n,r in speech['loaded_worker']['bound_images'].items():
        expected=next(k for k in profile['files'] if k.rsplit('/',1)[-1].lower()==n)
        assert normalize(r['path']).casefold()==(root+expected).casefold()
    assert [h for n,h in bindings.items() if n.endswith('/scripts/prove_native_operator_settings.py')]==[files['scripts/prove_native_operator_settings.py']['sha256']]
    observed=memory_evidence(memory,speech,frozen,freeze_raw,retired,a.platform=='windows')
    settings=settings_evidence(z.read,prefix+'memory/speech/',speech)
    for case in speech['cases']:
        assert z.read(prefix+'memory/speech/'+case['name']+'.wav')==base.read(prefix+'settings/'+case['name']+'.wav')
    enrollment=bound('sdk/result.json');analyze(current.read,prefix+'sdk/',enrollment)
    if a.carrier_refresh:
        bound_result(enrollment,frozen,actual_carrier,retired)
        eb={normalize(n):h for n,h in enrollment['bindings'].items()}
        assert [h for n,h in eb.items() if n.endswith('/scripts/prove_native_session_enrollment.py')]==[files['scripts/prove_native_session_enrollment.py']['sha256']]
        # This archived harness, not the earlier metadata-blind fixture, ran.
        assert b"args={**args,'_host_now_ms':time.time_ns()//1_000_000}" in source.read('scripts/prove_native_session_enrollment.py')
    handoff=get('package/handoff.json');assert handoff['passed'] and not handoff['signed'] and not handoff['installed']
    assert handoff['sdk_revision']==pin['revision']
    assert not handoff['human_level_qualified'] and not handoff['ready_for_private_test_signing_review']
    assert handoff['checkpoint_freeze_sha256']==digest(freeze_raw)
    assert handoff['uid_read_observation_proof_sha256']==digest(current.read(prefix+'sdk/result.json'))
    assert handoff['memory_proof_sha256']==digest(z.read(prefix+'memory/result.json'))
    assert handoff['settings_proof_sha256']==digest(z.read(prefix+'memory/speech/result.json'))
    assert handoff['sampled_memory_lower_bound_bytes']==observed['sampled_peak_sum_rss_bytes']
    report={'passed':True,'scope':__doc__,'platform':a.platform,'carrier_only_refresh':a.carrier_refresh,'memory':observed,'settings':settings,
            'package':package_evidence(a.artifacts,handoff,frozen,profile,bound('checkpoint/settings.json'),get('package/descriptors.json'),a.platform),
            'source_sha256':digest(a.source.read_bytes()),'sampler_source_sha256':digest(a.sampler_source.read_bytes()),
            'baseline_evidence_sha256':digest(a.baseline_evidence.read_bytes()),'evidence_sha256':digest(a.evidence.read_bytes()),
            'audit_recipe_sha256':digest(Path(__file__).read_bytes()),'human_level_qualified':False}
    schema_root='package-schemas/' if 'packaging_parent' in stages else 'plugin/native/'
    for ref,h in report['package']['operation_schemas'].items():assert h==files[schema_root+ref]['sha256']
    if 'packaging_parent' in stages:
        parent_spec=json.loads(source.read('packaging-parent.json'))
        assert stages['packaging_parent']==parent_spec
        assert parent_spec['files']['evidence.zip']==digest(a.baseline_evidence.read_bytes())
        assert parent_spec['files']['run/checkpoint/freeze.json']==digest(freeze_raw)
        assert parent_spec['runtime_manifest_sha256']==frozen['runtime_manifest_sha256']
        assert parent_spec['carrier_sha256']==frozen['carrier_sha256']
        assert parent_spec['tts_sha256']==frozen['library_hashes']['native_pocket_resident.dll']
        assert report['package']['operation_schemas']==parent_spec['package_schemas']==handoff['operation_schema_files']
    with a.out.open('x') as f:json.dump(report,f,indent=2);f.write('\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
