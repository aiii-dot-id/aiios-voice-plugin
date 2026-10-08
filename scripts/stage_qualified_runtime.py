"""Pack measured native companion bytes using the pinned SDK, without release claims.

Carrier stays in the plugin, models remain declared data downloads. No model
load, signing, upload, installation, source rebuild or invented URL is performed.
"""
from scripts._assertions import require_assertions
require_assertions()
import argparse
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile

from scripts.build_plugin_carrier import verify_sdk, SDK_SOURCE
from scripts.package_native_runtime import refuse_interpreter_profile, verify
from scripts.runtime_limits import profile_limits
from scripts.rebind_signed_windows_runtime import SUBJECT, verify_authenticode
from scripts.check_native_binary_privacy import audit_release_images, release_owned_image, third_party_images
from scripts.windows_signing_targets import CARRIER, signing_targets


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
    return verify_authenticode(runtime, signing_targets(profile['files']) | {CARRIER}, signtool)


def authenticode_status(runtime, profile, observations):
    """Report Authenticode per PE image; never extend a subset's result to the runtime.

    The runtime claim covers every release-built PE image plus the carrier.
    Third-party images keep their vendor signatures, which this gate does
    not verify; they are reported as not verified rather than implied.
    """
    if profile['platform'] != 'windows':
        if observations:
            raise ValueError('Authenticode observation for a non-Windows runtime')
        return dict(authenticode_verified=False, authenticode_images={},
                    authenticode_unverified_release_images=[])
    names = [*profile['files'], 'aii-voice-t3.exe']
    by_path = {str(Path(runtime)/name): name for name in names}
    verified = set()
    for row in observations:
        name = by_path.get(row.get('path'))
        if (name is None or row.get('status') != 'Valid' or row.get('subject') != SUBJECT
                or not row.get('timestamp')):
            raise ValueError('Authenticode observation is not bound to a valid shipped image')
        verified.add(name)
    pe = sorted(n for n in names if n.lower().endswith(('.dll', '.exe')))
    unverified = [n for n in pe if release_owned_image(n) and n not in verified]
    return dict(authenticode_verified=not unverified,
                authenticode_images={n: 'verified_publisher' if n in verified else 'not_verified' for n in pe},
                authenticode_unverified_release_images=unverified,
                authenticode_scope='release-built PE images and carrier; third-party images keep vendor signatures not verified here')


def composition_coordinates(profile, variant_id=None):
    platform, arch = {('darwin', 'arm64'): ('macos', 'arm64'),
                      ('linux', 'amd64'): ('linux', 'x86_64'),
                      ('windows', 'amd64'): ('windows', 'x86_64')}[
                          profile['platform'], profile['arch']]
    variant = variant_id if variant_id is not None else platform+'-'+arch+'-native'
    if not isinstance(variant, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', variant):
        raise ValueError('explicit component-set variant ID is invalid')
    return platform, arch, variant


def directories(rows):
    """How many directories the tree holds, its root not counted."""
    found = set()
    for name in rows:
        parts = PurePosixPath(name).parts[:-1]
        found.update(parts[:n] for n in range(1, len(parts) + 1))
    return len(found)


def files_budget(files, dirs):
    """The least files budget under which the kit's packer and the host's reader take the tree.

    Both count a tree's directories with its files and admit, beside the
    files a budget names, a quarter as many members again. A budget of the
    file count alone is refused for a tree with more directories than a
    quarter of its files, which a packaged Core ML cache is. The budget is
    the packer's limit; what the package declares stays the file count.
    """
    budget = max(files, (files + dirs) * 4 // 5)
    while budget + budget // 4 < files + dirs:
        budget += 1
    return budget


def runtime_pack_limits(rows, max_compressed_bytes):
    """Declare the actual closure and an explicit release-owner archive budget.

    Native dependency closures and packaged Core ML caches do not share the
    old 128 MiB/eight-level fixture ceiling. The SDK still validates all bounds;
    neither a small fixture nor a compressed size is a measured memory reserve.
    """
    if type(max_compressed_bytes) is not int or max_compressed_bytes <= 0:
        raise ValueError('explicit positive compressed archive budget required')
    if (not rows or any(type(r.get('bytes')) is not int or r['bytes'] < 0 for r in rows.values())
            or sum(r['bytes'] for r in rows.values()) <= 0):
        raise ValueError('nonempty nonnegative runtime inventory with positive total required')
    return {
        'installed_bytes': sum(r['bytes'] for r in rows.values()),
        'files': len(rows),
        'files_budget': files_budget(len(rows), directories(rows)),
        'file_bytes': max(r['bytes'] for r in rows.values()),
        # Count the SDK archive root as well as every sealed member component.
        'depth': max(2, max(len(PurePosixPath(n).parts) + 1 for n in rows)),
        'compressed_bytes': max_compressed_bytes,
    }


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
    p.add_argument('--variant-id',help='Explicit component-set ID; omission retains the single-platform checkpoint ID')
    p.add_argument('--max-compressed-bytes',type=int,required=True,
                   help='Explicit archive budget in bytes; the SDK enforces it')
    p.add_argument('--third-party-images',type=Path,
                   help='aiii.voice.third-party-images.v1 declaration of vendor redistributables by exact sha256')
    p.add_argument('--third-party-images-sha256',help='Exact digest of the third-party image declaration')
    a=p.parse_args(); cp=a.checkpoint.resolve(); out=a.out.resolve()
    proof=audit_binding(a.audit,a.audit_sha256,a.runtime_sha256)
    frozen=json.loads((cp/'freeze.json').read_text())
    assert frozen['runtime_manifest_sha256']==a.runtime_sha256
    runtime=cp/'runtime'; profile=verify(runtime,a.runtime_sha256)
    # A checkpoint whose profile describes an interpreter is verified like any
    # other and is not staged: no release archive is made of a Python engine.
    refuse_interpreter_profile(profile)
    # Staging is where a checkpoint becomes a release archive. A profile that
    # states no time limits, or not every one, would ship whatever its carrier
    # compiled, said nowhere in the package: refused before anything is built.
    stated_limits=profile_limits(profile,released=True)
    windows=profile['platform']=='windows'
    platform,arch,variant=composition_coordinates(profile,a.variant_id); pin,_=verify_sdk()
    carrier=runtime/('aii-voice-t3.exe' if windows else 'aii-voice-t3');assert sha(carrier)==frozen['carrier_sha256']
    audit_checkpoint(proof,cp,carrier.name)
    assert json.loads((cp/'carrier-build.json').read_text())['sdk_revision']==pin['revision']
    privacy=audit_release_images(runtime,profile,carrier,
        third_party_images(a.third_party_images,a.third_party_images_sha256))
    signatures=windows_signatures(runtime,profile,a.signtool)
    authenticode=authenticode_status(runtime,profile,signatures)
    described = subprocess.run([str(carrier)], env={**os.environ, 'AIISDK_DESCRIBE': '1'},
                               capture_output=True, check=True, timeout=10)
    descriptors = json.loads(described.stdout)
    if not isinstance(descriptors, list) or not descriptors:
        raise ValueError('staged carrier did not expose its callable contract')
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
    limits=runtime_pack_limits(rows,a.max_compressed_bytes)
    declaration=json.loads(run('runtime-pack',[sdk,'runtime-pack','-dir',tree,'-o',archive,'-root','runtime',
        '-max-installed-bytes',limits['installed_bytes'],'-max-files',limits['files_budget'],
        '-max-file-bytes',limits['file_bytes'],'-max-compressed-bytes',limits['compressed_bytes'],
        '-max-depth',limits['depth']]))
    check_archive(archive,declaration,rows,windows=windows)
    assert verify(runtime,a.runtime_sha256)==profile
    assert sha(carrier)==frozen['carrier_sha256']
    audit_checkpoint(audit_binding(a.audit,a.audit_sha256,a.runtime_sha256),cp,carrier.name);verify_sdk()
    result=dict(passed=True,scope=__doc__,variant_id=variant,sdk_revision=pin['revision'],
        runtime_manifest_sha256=a.runtime_sha256,carrier_sha256=frozen['carrier_sha256'],
        checkpoint_freeze_sha256=sha(cp/'freeze.json'),qualification_sha256=a.audit_sha256,
        archive_budget=limits,
        # The time limits this runtime ships with, as its bound profile states them.
        runtime_limits=stated_limits,
        models=frozen['models'],descriptors=descriptors,
        # The archive travels with its unmodified receipt across machines.
        qualification_scope=proof['scope'],runtime_archive=dict(path=archive.name,**declaration),
        source_sha256=sha(__file__),models_in_archive=False,carrier_in_archive=False,
        authenticode_observations=signatures,**authenticode,
        native_image_privacy=privacy,third_party_images_sha256=a.third_party_images_sha256,
        signed=False,installed=False,published=False,
        release_status=dict(runtime_archive='inventory_and_bytes_verified',
            qualification='provided_audit_passed_at_its_declared_scope',
            release_signature='not_performed_by_runtime_staging',
            installed_journey='not_performed_by_runtime_staging',
            publication='not_performed_by_runtime_staging'))
    with (out/'result.json').open('x') as f: json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps(result))


if __name__=='__main__':main()
