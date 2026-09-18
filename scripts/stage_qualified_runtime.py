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
from scripts.rebind_signed_windows_runtime import OWNED, verify_authenticode


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


def audit_checkpoint(proof, checkpoint, carrier_name):
    """A successful run must bind this carrier too, not merely its libraries.

    Evidence may be copied from another machine. Match its declared checkpoint
    prefix, not the local staging path or arbitrary matching basenames.
    """
    prefix = str(proof.get('checkpoint', '')).replace('\\', '/').rstrip('/')
    if not prefix:
        raise ValueError('qualification checkpoint missing')
    bound = {name.replace('\\', '/'): digest for name, digest in proof.get('bindings', {}).items()}
    for name in ('freeze.json', 'carrier-build.json', 'runtime/voice-runtime.json', 'runtime/'+carrier_name):
        if bound.get(prefix+'/'+name) != sha(checkpoint/name):
            raise ValueError('qualification checkpoint binding differs: '+name)


def windows_signatures(runtime, profile, signtool):
    if profile['platform'] != 'windows':
        if signtool is not None:
            raise ValueError('Authenticode verifier supplied for non-Windows runtime')
        return []
    if os.name != 'nt' or signtool is None:
        raise ValueError('Windows staging requires native Authenticode verification')
    if not OWNED <= set(profile['files']):
        raise ValueError('Windows owned-image inventory incomplete')
    return verify_authenticode(runtime, OWNED | {'aii-voice-t3.exe'}, signtool)


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
    p.add_argument('--go',type=Path,default=Path('/usr/local/go1.27/bin/go'))
    p.add_argument('--signtool',type=Path,help='Required on Windows; validates publisher, trust and timestamp')
    a=p.parse_args(); cp=a.checkpoint.resolve(); out=a.out.resolve()
    proof=audit_binding(a.audit,a.audit_sha256,a.runtime_sha256)
    frozen=json.loads((cp/'freeze.json').read_text())
    assert frozen['runtime_manifest_sha256']==a.runtime_sha256
    runtime=cp/'runtime'; profile=verify(runtime,a.runtime_sha256)
    windows=profile['platform']=='windows'
    platform,arch={('darwin','arm64'):('macos','arm64'),('linux','amd64'):('linux','x86_64'),
                   ('windows','amd64'):('windows','x86_64')}[(profile['platform'],profile['arch'])]
    variant=platform+'-'+arch+'-native'; pin,_=verify_sdk()
    carrier=runtime/('aii-voice-t3.exe' if windows else 'aii-voice-t3');assert sha(carrier)==frozen['carrier_sha256']
    audit_checkpoint(proof,cp,carrier.name)
    assert json.loads((cp/'carrier-build.json').read_text())['sdk_revision']==pin['revision']
    signatures=windows_signatures(runtime,profile,a.signtool)
    out.mkdir(parents=True,exist_ok=False);tree=out/'companion-tree';tree.mkdir()
    rows={**profile['files'],'voice-runtime.json':dict(sha256=a.runtime_sha256,bytes=(runtime/'voice-runtime.json').stat().st_size,executable=False)}
    for name,row in rows.items():
        dest=tree/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(runtime/name,dest);dest.chmod(0o755 if row['executable'] and not windows else 0o644)
        assert sha(dest)==row['sha256']
    env={**os.environ,'GOTOOLCHAIN':'local','GOWORK':'off','GOPROXY':'off','GOSUMDB':'off','CGO_ENABLED':'0','GOMODCACHE':str(a.go_modcache.resolve())}
    def run(name,cmd):
        r=subprocess.run(list(map(str,cmd)),cwd=SDK_SOURCE,env=env,capture_output=True,timeout=180)
        (out/(name+'.stdout')).write_bytes(r.stdout);(out/(name+'.stderr')).write_bytes(r.stderr)
        if r.returncode: raise RuntimeError(name+' failed; output retained')
        return r.stdout
    sdk=out/('aiisdk.exe' if os.name=='nt' else 'aiisdk')
    run('sdk-build',[a.go,'build','-trimpath','-buildvcs=false','-o',sdk,'./cmd/aiisdk'])
    archive=out/(variant+'-runtime.tar.gz')
    declaration=json.loads(run('runtime-pack',[sdk,'runtime-pack','-dir',tree,'-o',archive,'-root','runtime',
        '-max-installed-bytes',sum(r['bytes'] for r in rows.values()),'-max-files',len(rows),
        '-max-file-bytes',max(r['bytes'] for r in rows.values()),'-max-compressed-bytes','128M','-max-depth','8']))
    check_archive(archive,declaration,rows,windows=windows)
    assert verify(runtime,a.runtime_sha256)==profile
    assert sha(carrier)==frozen['carrier_sha256']
    audit_checkpoint(audit_binding(a.audit,a.audit_sha256,a.runtime_sha256),cp,carrier.name);verify_sdk()
    result=dict(passed=True,scope=__doc__,variant_id=variant,sdk_revision=pin['revision'],
        runtime_manifest_sha256=a.runtime_sha256,carrier_sha256=frozen['carrier_sha256'],
        checkpoint_freeze_sha256=sha(cp/'freeze.json'),qualification_sha256=a.audit_sha256,
        # The archive travels with its unmodified receipt across machines.
        qualification_scope=proof['scope'],runtime_archive=dict(path=archive.name,**declaration),
        source_sha256=sha(__file__),models_in_archive=False,carrier_in_archive=False,
        authenticode_verified=windows,authenticode_observations=signatures,
        signed=False,installed=False,published=False,
        release_status=dict(runtime_archive='inventory_and_bytes_verified',
            qualification='provided_audit_passed_at_its_declared_scope',
            release_signature='not_performed_by_runtime_staging',
            installed_journey='not_performed_by_runtime_staging',
            publication='not_performed_by_runtime_staging'))
    with (out/'result.json').open('x') as f: json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps(result))


if __name__=='__main__':main()
