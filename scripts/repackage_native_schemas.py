"""Package-only schema repair; preserve every existing executable and dependency.

No signing, installation, native rebuild or inference qualification. A new
version is assembled from a verified prior bundle and the actual schema files.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from scripts.audit_native_desktop_packages import tar_files
from scripts.build_plugin_carrier import ROOT,SDK_SOURCE,verify_sdk


def digest(raw):return hashlib.sha256(raw).hexdigest()


def read_package(path,expected,*,platform_signature=False):
    assert digest(path.read_bytes())==expected
    members,_=tar_files(path);prefix=path.name.removesuffix('.aiiospkg')+'/'
    manifest=json.loads(members.pop(prefix+'manifest.json'))
    if platform_signature:
        # Structural extraction only. Callers still need the real host's
        # cryptographic/revocation verdict bound to this exact archive hash.
        env=json.loads(members.pop(prefix+'signatures/platform.sig'))
        assert env['artifact_kind']=='plugin.platform_release'
    assert all(n.startswith(prefix+'install-root/') for n in members)
    files={n.removeprefix(prefix+'install-root/'):raw for n,raw in members.items()}
    aggregate=b''.join((n+'\0'+digest(raw)+'\n').encode() for n,raw in sorted(files.items()))
    assert manifest['package_hash']=='sha256:'+digest(aggregate)
    for v in manifest['variants']:assert v['artifact_hash']=='sha256:'+digest(files[v['entrypoint']])
    for i in manifest['interfaces']['core']:
        assert i['schema_hash']=='sha256:'+digest(files[f'interfaces/{i["id"]}.v{i["version"]}.schema.json'])
    return manifest,files


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('parent','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--version',required=True);a=p.parse_args()
    a.parent=a.parent.resolve();a.out=a.out.resolve()
    pin,_=verify_sdk();handoff=json.loads((a.parent/'handoff.json').read_text())
    assert handoff['passed'] and handoff['sdk_revision']==pin['revision']
    source=a.parent/handoff['bundle']['bundle'];manifest,files=read_package(source,handoff['bundle']['sha256'])
    assert a.version!=manifest['version']
    cfg=json.loads((a.parent/'plugin.json').read_text());cfg['version']=a.version
    a.out.mkdir(parents=True,exist_ok=False)
    variants={v['variant_id']:v for v in manifest['variants']}
    for v in cfg['variants']:
        old=variants[v['variant_id']];v['artifact']='payloads/'+v['variant_id']
        target=a.out/v['artifact'];target.parent.mkdir(exist_ok=True);target.write_bytes(files[old['entrypoint']])
    # Read the descriptor from the verified package, never guess argument names
    # from a summary or silently alter the carrier's own declarations.
    descriptors=[]
    for i in manifest['interfaces']['core']:
        descriptors.extend(json.loads(files[f'interfaces/{i["id"]}.v{i["version"]}.schema.json']))
    descriptors.sort(key=lambda d:d['id'])
    schemas={}
    for d in descriptors:
        for key in ('input','output'):
            ref=d.get(key)
            if not ref:continue
            assert ref.startswith('schemas/') and '..' not in Path(ref).parts
            raw=(ROOT/'plugin/native'/ref).read_bytes()
            target=a.out/ref;target.parent.mkdir(exist_ok=True);target.write_bytes(raw);schemas[ref]=digest(raw)
    assert len(schemas)==5
    (a.out/'plugin.json').write_text(json.dumps(cfg,indent=2)+'\n')
    (a.out/'descriptors.json').write_text(json.dumps(descriptors,indent=2)+'\n')
    tool=a.out/'assemble';env={**os.environ,'GOTOOLCHAIN':'local','GOWORK':'off','GOPROXY':'off'}
    build=subprocess.run(['/usr/local/go1.27/bin/go','build','-trimpath','-buildvcs=false','-o',str(tool),
                          str(ROOT/'scripts/private_cp1_package.go')],cwd=SDK_SOURCE,env=env,capture_output=True,timeout=60)
    (a.out/'build.stdout').write_bytes(build.stdout);(a.out/'build.stderr').write_bytes(build.stderr);assert build.returncode==0
    run=subprocess.run([str(tool),str(a.out)],capture_output=True,timeout=30)
    (a.out/'assemble.stdout').write_bytes(run.stdout);(a.out/'assemble.stderr').write_bytes(run.stderr);assert run.returncode==0
    assembly=json.loads(run.stdout)
    new_manifest,new=read_package(a.out/assembly['bundle'],assembly['sha256'])
    assert set(new)==set(files)|set(schemas)
    assert all(new[n]==raw for n,raw in files.items()),'schema repair changed an existing payload/declaration'
    assert all(digest(new[n])==h for n,h in schemas.items())
    assert new_manifest['variants']==manifest['variants'] and new_manifest['interfaces']==manifest['interfaces']
    report={'passed':True,'scope':__doc__,'signed':False,'installed':False,'published':False,
            'human_level_qualified':False,'host_schema_gate_pending':True,'bundle':assembly,
            'sdk_revision':pin['revision'],'parent_bundle_sha256':handoff['bundle']['sha256'],
            'parent_handoff_sha256':digest((a.parent/'handoff.json').read_bytes()),
            'variants':[v['variant_id'] for v in manifest['variants']],
            'new_members':schemas,'unchanged_existing_members':len(files),
            'assembler_sha256':digest((ROOT/'scripts/private_cp1_package.go').read_bytes()),
            'script_sha256':digest(Path(__file__).read_bytes()),
            'runtime_and_models_unchanged':True,'companions':handoff.get('companions',handoff.get('runtime_archive'))}
    (a.out/'handoff.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':main()
