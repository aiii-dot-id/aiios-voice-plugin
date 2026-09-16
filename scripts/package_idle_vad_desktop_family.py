"""Assemble the three executed VAD-repair runtimes into one private T3 family.

No native rebuild, model copy, signing, installation, URL publication or change
to earlier packages. The public download, physical-audio and quality gates
remain open. This is an explicitly versioned checkpoint, not a release claim.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import tarfile
from scripts.build_plugin_carrier import ROOT, SDK_SOURCE, verify_sdk
from scripts.repackage_native_schemas import read_package
from scripts.audit_native_desktop_family import audit as audit_family, executable_binding
from scripts.package_desktop_metal_checkpoint import emit, put, sha
from scripts.prepare_desktop_release_notices import validate_runtime_archive

PARENT=ROOT/'deliverables/checkpoints/desktop-metal-unified-20260914-r1'
PARENT_SHA='1196aa5de3f1a30ee4443f5bc25e887e805b4e61b58acca4d1961f4a45cab005'
REPAIR=ROOT/'deliverables/native-vad-idle-reset-20260914-r1'
HANDOFF_SHA='735e77f0bb2f779c8710e9f7939bf3e41bf2113aef9f2bf10947f7aa47959712'
TARGETS={'mac':'macos-arm64-native','linux':'linux-x86_64-native','windows':'windows-x86_64-native'}
CHANGES={'mac':{'bin/aii_voice_worker','lib/libaii_voice_runtime.dylib'},
         'linux':{'lib/libaii_voice_runtime.so'},'windows':{'bin/aii_voice_runtime.dll'}}


def profiles_equal_except_images(old,new,allowed):
    if set(old['files'])!=set(new['files']): raise ValueError('runtime member set changed')
    if {n for n in old['files'] if old['files'][n]!=new['files'][n]}!=allowed:
        raise ValueError('unqualified runtime image delta')
    if {k:v for k,v in old.items() if k!='files'}!={k:v for k,v in new.items() if k!='files'}:
        raise ValueError('runtime contract changed')


def delta(old,before,new,after,cfg,carriers):
    variants={v['variant_id']:v for v in old['variants']}
    allowed={v['entrypoint'] for v in variants.values()}|{'runtime.json'}
    if set(before)!=set(after) or any(after[n]!=b for n,b in before.items() if n not in allowed):
        raise ValueError('shared settings/models/schemas/accelerator/authority changed')
    wanted=copy.deepcopy(old);wanted.update(version=cfg['version'],package_hash=new['package_hash'])
    from hashlib import sha256
    for v in wanted['variants']:v['artifact_hash']='sha256:'+sha256(carriers[v['variant_id']]).hexdigest()
    if wanted!=new:raise ValueError('unexpected manifest delta')
    for key,v in variants.items():
        if after[v['entrypoint']]!=carriers[key]:raise ValueError('wrong bound carrier')
    if json.loads(after['runtime.json'])['runtimes']!=cfg['runtimes']:raise ValueError('runtime declaration differs')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--version',required=True);a=p.parse_args();out=a.out.resolve()
    if out.exists():raise ValueError('fresh output required')
    pin,_=verify_sdk();parent=json.loads((PARENT/'handoff.json').read_text())
    old,before=read_package(PARENT/parent['bundle']['bundle'],PARENT_SHA)
    if sha(REPAIR/'desktop-runtime-handoff-r1.json')!=HANDOFF_SHA:raise ValueError('runtime handoff changed')
    combined=json.loads((REPAIR/'desktop-runtime-handoff-r1.json').read_text())
    if not combined['passed'] or any(combined[k] for k in ('signed','installed','published','source_promoted')):
        raise ValueError('not the isolated executed checkpoints')
    cfg=json.loads((PARENT/'plugin.json').read_text())
    if a.version==cfg['version'] or not a.version.startswith('0.1.0-native-cp3-'):
        raise ValueError('explicit fresh private checkpoint version required')
    cfg['version']=a.version
    carriers={};companions={};holds={};bindings={};evidence={}
    for platform,key in TARGETS.items():
        row=combined['platforms'][platform];folder=REPAIR/('runtime-handoff-'+platform+'-r1')
        hand=json.loads((folder/'handoff.json').read_text())
        archive=folder/'runtime.tar.gz';carrier=folder/('aii-voice-t3.exe' if platform=='windows' else 'aii-voice-t3')
        if sha(archive)!=row['archive_sha256'] or sha(carrier)!=row['carrier_sha256']:
            raise ValueError('executed handoff byte drift')
        if hand['sdk_revision']!=pin['revision'] or parent['sdk_revision']!=pin['revision']:
            raise ValueError('SDK revision differs')
        with tarfile.open(archive) as tf:profile=json.load(tf.extractfile('runtime/voice-runtime.json'))
        with tarfile.open(parent['companions'][key]['path']) as tf:old_profile=json.load(tf.extractfile('runtime/voice-runtime.json'))
        profiles_equal_except_images(old_profile,profile,CHANGES[platform])
        validate_runtime_archive(dict(archive={**hand['runtime'],'path':str(archive)},files=profile['files'],
                                      runtime_manifest_sha256=row['runtime_manifest_sha256']))
        raw=carrier.read_bytes();executable_binding(raw,row['runtime_manifest_sha256']);carriers[key]=raw
        declaration=next(r for r in cfg['runtimes'] if r['variant_id']==key)
        for k in ('files','installed_bytes','inventory_sha256','sha256','size'):declaration[k]=hand['runtime'][k]
        declaration['url']='https://checkpoint.invalid/runtime/'+key+'-idlevad-'+declaration['sha256']+'.tar.gz'
        companions[key]={**declaration,'path':str(archive)}
        evidence[key]=dict(execution_audit_sha256=row['execution_audit_sha256'],changed_runtime_images=sorted(CHANGES[platform]),
                           runtime_manifest_sha256=row['runtime_manifest_sha256'],carrier_sha256=row['carrier_sha256'])
        bindings[str(archive)]=sha(archive);bindings[str(carrier)]=sha(carrier);bindings[str(folder/'handoff.json')]=sha(folder/'handoff.json')
    out.mkdir(parents=True)
    for v in cfg['variants']:v['artifact']='payloads/'+v['variant_id'];put(out/v['artifact'],carriers[v['variant_id']])
    descriptors=[]
    for i in old['interfaces']['core']:descriptors.extend(json.loads(before[f'interfaces/{i["id"]}.v{i["version"]}.schema.json']))
    for n,b in before.items():
        if n.startswith('schemas/'):put(out/n,b)
    emit(out/'plugin.json',cfg);emit(out/'descriptors.json',sorted(descriptors,key=lambda d:d['id']))
    # Describe does not open devices or load models. The native byte binding was
    # checked on all targets; this local check also pins their shared contract.
    describe=subprocess.run([str(REPAIR/'runtime-handoff-mac-r1/aii-voice-t3')],env={**os.environ,'AIISDK_DESCRIBE':'1'},capture_output=True,timeout=10)
    put(out/'describe.stdout',describe.stdout);put(out/'describe.stderr',describe.stderr)
    if describe.returncode or json.loads(describe.stdout)!=sorted(descriptors,key=lambda d:d['id']):raise ValueError('carrier contract differs')
    env={**os.environ,'GOTOOLCHAIN':'local','GOWORK':'off','GOPROXY':'off'}
    for name,command in (
        ('build',['/usr/local/go1.27/bin/go','build','-trimpath','-buildvcs=false','-o',str(out/'assemble'),str(ROOT/'scripts/private_cp1_package.go')]),
        ('assemble',[str(out/'assemble'),str(out)])):
        run=subprocess.run(command,cwd=SDK_SOURCE,env=env,capture_output=True,timeout=90)
        put(out/(name+'.stdout'),run.stdout);put(out/(name+'.stderr'),run.stderr)
        if run.returncode:raise ValueError(name+' failed; logs retained')
    assembly=json.loads(run.stdout);new,after=read_package(out/assembly['bundle'],assembly['sha256'])
    delta(old,before,new,after,cfg,carriers)
    result=dict(passed=True,scope=__doc__,bundle=assembly,version=a.version,sdk_revision=pin['revision'],
                parent_package_sha256=PARENT_SHA,combined_execution_handoff_sha256=HANDOFF_SHA,
                companions=companions,variants=sorted(carriers),execution=evidence,
                settings_unchanged=True,models_unchanged=True,schemas_unchanged=True,models_copied=False,
                signed=False,installed=False,published=False,human_level_qualified=False,public_distribution_ready=False,
                source_promoted=False,performance_promotion=False,script_sha256=sha(__file__),
                open_gates=['Real asset URLs and guarded downloads','Notice inventory rebind and distribution review',
                            'T3 signing and installed browser/identity qualification','Premature pause and STT quality gaps',
                            'Windows startup and first-request optimization','Full physical Pixel/iPhone accelerated composition',
                            'Human-level and multilingual qualification'])
    emit(out/'handoff.json',result);audit=audit_family(out,macos_backend='metal');emit(out/'independent-package-audit-r1.json',audit)
    for n,h in bindings.items():
        if sha(Path(n))!=h:raise ValueError('handoff changed during assembly')
    if sha(PARENT/parent['bundle']['bundle'])!=PARENT_SHA:raise ValueError('parent changed during assembly')
    verify_sdk();print(json.dumps(dict(passed=True,bundle=assembly,settings=audit['settings'],voices=audit['voices'],variants=audit['variants']),indent=2))


if __name__=='__main__':main()
