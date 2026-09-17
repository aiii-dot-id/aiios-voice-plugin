"""Pack measured native companion bytes using the pinned SDK, without release claims.

Carrier stays in the plugin, models remain declared data downloads. No model
load, signing, upload, installation, source rebuild or invented URL is performed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile

from scripts.build_plugin_carrier import verify_sdk, SDK_SOURCE
from scripts.package_native_runtime import verify


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def audit_binding(path, digest, runtime_digest):
    if sha(path) != digest: raise ValueError('qualification evidence hash differs')
    proof = json.loads(path.read_text())
    if not proof['passed'] or proof['runtime_manifest_sha256'] != runtime_digest:
        raise ValueError('qualification does not bind this runtime')
    return proof


def check_archive(archive, declaration, rows, windows=False):
    if sha(archive) != declaration['sha256'] or archive.stat().st_size != declaration['size']:
        raise ValueError('archive digest/size differs')
    with tarfile.open(archive, 'r:gz') as t:
        members = t.getmembers(); names = [m.name for m in members]
        if len(names) != len(set(names)) or not all(m.isfile() or m.isdir() for m in members):
            raise ValueError('nonregular or duplicate archive member')
        dirs = {'runtime'}
        for name in rows:
            dirs.update(str(p) for p in PurePosixPath('runtime/'+name).parents if str(p) != '.')
        if {m.name.rstrip('/') for m in members if m.isdir()} != dirs:
            raise ValueError('archive directory census differs')
        if {m.name for m in members if m.isfile()} != {'runtime/'+n for n in rows} | {'runtime/inventory.json'}:
            raise ValueError('archive file census differs')
        if declaration['files'] != len(rows) or declaration['installed_bytes'] != sum(r['bytes'] for r in rows.values()):
            raise ValueError('archive installation budget differs')
        raw = t.extractfile('runtime/inventory.json').read()
        if hashlib.sha256(raw).hexdigest() != declaration['inventory_sha256']:
            raise ValueError('SDK inventory digest differs')
        observed = []
        for name, row in sorted(rows.items()):
            member = t.getmember('runtime/'+name)
            executable = row['executable'] and not windows
            h = hashlib.sha256(t.extractfile(member).read()).hexdigest()
            if h != row['sha256'] or member.size != row['bytes'] or member.mode != (0o755 if executable else 0o644):
                raise ValueError('archive member differs: '+name)
            observed.append(dict(path=name,size=member.size,sha256='sha256:'+h,mode='exec' if executable else 'file'))
        if json.loads(raw)['files'] != observed: raise ValueError('SDK inventory content differs')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ('checkpoint','audit','out','go-modcache'): p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--audit-sha256',required=True); p.add_argument('--runtime-sha256',required=True)
    a=p.parse_args(); cp=a.checkpoint.resolve(); out=a.out.resolve()
    proof=audit_binding(a.audit,a.audit_sha256,a.runtime_sha256)
    frozen=json.loads((cp/'freeze.json').read_text())
    assert frozen['runtime_manifest_sha256']==a.runtime_sha256
    runtime=cp/'runtime'; profile=verify(runtime,a.runtime_sha256)
    # Windows PE signatures must be settled before immutable companion hashes.
    if profile['platform']=='windows': raise ValueError('Windows requires the Authenticode release-signing stage first')
    platform,arch={('darwin','arm64'):('macos','arm64'),('linux','amd64'):('linux','x86_64')}[(profile['platform'],profile['arch'])]
    variant=platform+'-'+arch+'-native'; pin,_=verify_sdk()
    carrier=runtime/'aii-voice-t3';assert sha(carrier)==frozen['carrier_sha256']
    assert json.loads((cp/'carrier-build.json').read_text())['sdk_revision']==pin['revision']
    out.mkdir(parents=True,exist_ok=False);tree=out/'companion-tree';tree.mkdir()
    rows={**profile['files'],'voice-runtime.json':dict(sha256=a.runtime_sha256,bytes=(runtime/'voice-runtime.json').stat().st_size,executable=False)}
    for name,row in rows.items():
        dest=tree/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(runtime/name,dest);dest.chmod(0o755 if row['executable'] else 0o644)
        assert sha(dest)==row['sha256']
    env={**os.environ,'GOTOOLCHAIN':'local','GOWORK':'off','GOPROXY':'off','GOSUMDB':'off','CGO_ENABLED':'0','GOMODCACHE':str(a.go_modcache.resolve())}
    def run(name,cmd):
        r=subprocess.run(list(map(str,cmd)),cwd=SDK_SOURCE,env=env,capture_output=True,timeout=180)
        (out/(name+'.stdout')).write_bytes(r.stdout);(out/(name+'.stderr')).write_bytes(r.stderr)
        if r.returncode: raise RuntimeError(name+' failed; output retained')
        return r.stdout
    sdk=out/'aiisdk'
    run('sdk-build',['/usr/local/go1.27/bin/go','build','-trimpath','-buildvcs=false','-o',sdk,'./cmd/aiisdk'])
    archive=out/(variant+'-runtime.tar.gz')
    declaration=json.loads(run('runtime-pack',[sdk,'runtime-pack','-dir',tree,'-o',archive,'-root','runtime',
        '-max-installed-bytes',sum(r['bytes'] for r in rows.values()),'-max-files',len(rows),
        '-max-file-bytes',max(r['bytes'] for r in rows.values()),'-max-compressed-bytes','128M','-max-depth','8']))
    check_archive(archive,declaration,rows)
    assert verify(runtime,a.runtime_sha256)==profile
    assert sha(carrier)==frozen['carrier_sha256']
    audit_binding(a.audit,a.audit_sha256,a.runtime_sha256);verify_sdk()
    result=dict(passed=True,scope=__doc__,variant_id=variant,sdk_revision=pin['revision'],
        runtime_manifest_sha256=a.runtime_sha256,carrier_sha256=frozen['carrier_sha256'],
        checkpoint_freeze_sha256=sha(cp/'freeze.json'),qualification_sha256=a.audit_sha256,
        qualification_scope=proof['scope'],runtime_archive=dict(path=str(archive),**declaration),
        source_sha256=sha(__file__),models_in_archive=False,carrier_in_archive=False,
        signed=False,installed=False,published=False,
        release_status=dict(runtime_archive='inventory_and_bytes_verified',
            qualification='provided_audit_passed_at_its_declared_scope',
            release_signature='not_performed_by_runtime_staging',
            installed_journey='not_performed_by_runtime_staging',
            publication='not_performed_by_runtime_staging'))
    with (out/'result.json').open('x') as f: json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps(result))


if __name__=='__main__':main()
