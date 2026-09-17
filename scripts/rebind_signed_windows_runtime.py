"""Rebind Authenticode-signed components without treating signatures as speech proof.

Run on Windows after sign_windows_native_runtime.ps1. This copies verified
components into a NEW checkpoint, rebuilds its exact-inventory carrier, and
leaves that carrier unsigned. Carrier Authenticode, same-byte speech gates,
T3 signing, final companion packing and publication remain separate gates.
No source checkpoint, model, installed identity or credential is modified.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess

from scripts.native_checkpoint_binding import sha, verify_checkpoint
from scripts.package_native_runtime import bind_carrier, runtime_inventory, verify

SUBJECT = 'CN=AIII, O=AIII, L=Westford, S=Massachusetts, C=US'
OWNED = frozenset(('bin/aiii_uid_frontend.dll', 'bin/aii_native_asr.dll',
    'bin/aii_native_endpoint.dll', 'bin/aii_native_uid.dll', 'bin/aii_native_vad.dll',
    'bin/aii_voice_runtime.dll', 'bin/aii_voice_worker.exe', 'bin/native_pocket_resident.dll'))


def require(ok, why):
    if not ok:
        raise ValueError(why)


def signing_only_change(before, after):
    """Accept only PE checksum/certificate-directory changes plus appended WIN_CERTIFICATE.

    This is a content-preservation check, NOT signature verification. WinVerifyTrust
    and SignTool must independently accept the signature and timestamp.
    """
    def layout(raw):
        require(len(raw) >= 64 and raw[:2] == b'MZ', 'invalid PE DOS header')
        pe = struct.unpack_from('<I', raw, 60)[0]
        require(pe >= 64 and pe + 24 <= len(raw) and raw[pe:pe+4] == b'PE\0\0', 'invalid PE header')
        optional = pe + 24
        size = struct.unpack_from('<H', raw, pe+20)[0]
        require(size >= 152 and optional + size <= len(raw), 'invalid PE optional header')
        magic = struct.unpack_from('<H', raw, optional)[0]
        require(magic in (0x10b, 0x20b), 'unsupported PE layout')
        directories = optional + (96 if magic == 0x10b else 112)
        count = struct.unpack_from('<I', raw, directories-4)[0]
        cert = directories + 4*8
        require(count >= 5 and cert+8 <= optional+size, 'PE security directory missing')
        return optional+64, cert, struct.unpack_from('<II', raw, cert)
    checksum, directory, old_cert = layout(before)
    check2, dir2, (offset, size) = layout(after)
    require((checksum, directory) == (check2, dir2) and old_cert == (0, 0), 'parent already signed or PE layout changed')
    aligned = (len(before)+7)//8*8
    require(offset == aligned and size >= 8 and offset+size == len(after), 'certificate not appended exactly')
    require(after[len(before):offset] == b'\0'*(offset-len(before)), 'nonzero signing alignment')
    # Every byte of executable code, data and pre-existing overlay is preserved.
    normal = bytearray(after[:len(before)])
    normal[checksum:checksum+4] = before[checksum:checksum+4]
    normal[directory:directory+8] = before[directory:directory+8]
    require(bytes(normal) == before, 'signing changed executable content')
    cursor = offset
    while cursor < len(after):
        require(cursor+8 <= len(after), 'truncated certificate header')
        length, revision, kind = struct.unpack_from('<IHH', after, cursor)
        require(length >= 8 and revision == 0x200 and kind == 2, 'invalid WIN_CERTIFICATE')
        end = cursor + length
        padded = (end+7)//8*8
        require(end <= len(after) and padded <= len(after) and
                after[end:padded] == b'\0'*(padded-end), 'invalid certificate length/padding')
        cursor = padded


def validate_report(report, parent_digest, before, after):
    require(report.get('passed') is True and not report.get('error'), 'signing stage not successful')
    require(report.get('parent_runtime_sha256') == parent_digest, 'signing parent differs')
    require(all(report.get(k) is False for k in
                ('t3_signed', 'runtime_rebound', 'carrier_rebuilt', 'qualified_after_signing')), 'unexpected signing stage state')
    rows = report.get('signed_files', [])
    require(len(rows) == len(OWNED) and {r['path'] for r in rows} == OWNED, 'signing census differs')
    require(set(before) == set(after) and OWNED <= set(before), 'runtime file census differs')
    for name in before:
        if name not in OWNED:
            require(before[name] == after[name], 'vendor/resource drift: '+name)
    for row in rows:
        name = row['path']
        require(row['before_sha256'] == before[name]['sha256'] and
                row['sha256'] == after[name]['sha256'] and row['bytes'] == after[name]['bytes'], 'signed byte binding differs: '+name)
        action=row.get('action','signed')
        require(action in ('signed','retained_verified'), 'unknown signing action: '+name)
        if action=='retained_verified':
            require(before[name]==after[name], 'retained signed component changed: '+name)
        else:
            require(before[name]['sha256'] != after[name]['sha256'], 'signature bytes missing: '+name)
        require(row['subject'] == SUBJECT and bool(row['timestamp_subject']), 'publisher/timestamp differs: '+name)
        require(before[name]['executable'] == after[name]['executable'], 'file mode changed: '+name)


def verify_authenticode(runtime, names, signtool):
    require(os.name == 'nt', 'Authenticode release verification requires Windows')
    # Literal PowerShell arguments; paths cannot inject statements. Only public
    # certificate metadata is returned, never Azure credentials or cache files.
    quoted = ','.join("'"+str(runtime/n).replace("'", "''")+"'" for n in sorted(names))
    script = "$ErrorActionPreference='Stop'; @("+quoted+") | ForEach-Object { $s=Get-AuthenticodeSignature -LiteralPath $_; " \
        "[pscustomobject]@{path=$_;status=[string]$s.Status;subject=$s.SignerCertificate.Subject;timestamp=$s.TimeStamperCertificate.Subject} } | ConvertTo-Json -Compress"
    raw = subprocess.check_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], timeout=120)
    rows = json.loads(raw.decode('utf-8-sig'))
    if isinstance(rows, dict) and len(names) == 1:
        rows = [rows]  # Windows PowerShell serializes a one-element pipeline as an object.
    require(isinstance(rows, list) and len(rows) == len(names) and
            {r['path'] for r in rows} == {str(runtime/n) for n in names}, 'signature observation census differs')
    for row in rows:
        require(row['status'] == 'Valid' and row['subject'] == SUBJECT and row['timestamp'], 'OS rejected publisher/timestamp: '+row['path'])
        subprocess.run([str(signtool), 'verify', '/pa', '/all', '/tw', row['path']], check=True, timeout=120, capture_output=True)
    return rows


def prepare(parent, signing, out, go, signtool):
    require(not out.exists(), 'fresh output required')
    require(not parent.is_symlink() and not signing.is_symlink(), 'linked stage refused')
    frozen_path = parent/'freeze.json'; parent_sha = sha(frozen_path)
    frozen = json.loads(frozen_path.read_text())
    profile = verify(parent/'runtime', frozen['runtime_manifest_sha256'])
    require(profile['platform'] == 'windows' and profile['arch'] == 'amd64', 'not a Windows checkpoint')
    old_build = json.loads((parent/'carrier-build.json').read_text())
    require(old_build['carrier_sha256'] == sha(parent/'runtime/aii-voice-t3.exe') == frozen['carrier_sha256'], 'parent carrier differs')
    require(old_build['runtime_manifest_sha256'] == frozen['runtime_manifest_sha256'], 'parent carrier/runtime differs')
    report_path = signing/'result.json'; report_sha = sha(report_path)
    # Windows PowerShell 5 writes UTF-8 with a BOM for Set-Content -Encoding UTF8.
    # Hash the original bytes; accept that encoding without rewriting evidence.
    report = json.loads(report_path.read_text(encoding='utf-8-sig')); signed = signing/'runtime'
    require(not signed.is_symlink(), 'linked signed runtime refused')
    require(sha(signed/'voice-runtime.json') == frozen['runtime_manifest_sha256'], 'signing stage manifest changed')
    require(sha(signed/'aii-voice-t3.exe') == frozen['carrier_sha256'], 'unexpected prior carrier replacement')
    after = runtime_inventory(signed, target_platform='windows')
    validate_report(report, frozen['runtime_manifest_sha256'], profile['files'], after)
    for row in report['signed_files']:
        name=row['path']
        before_raw=(parent/'runtime'/name).read_bytes();after_raw=(signed/name).read_bytes()
        if row.get('action','signed')=='retained_verified':require(before_raw==after_raw,'retained executable content changed')
        else:signing_only_change(before_raw,after_raw)
    signatures = verify_authenticode(signed, OWNED, signtool)
    # Recheck after trust verification, before publishing an output tree.
    require(runtime_inventory(signed, target_platform='windows') == after and sha(report_path) == report_sha, 'signed source changed during trust verification')
    out.mkdir(parents=True); runtime = out/'runtime'; runtime.mkdir()
    for name, row in after.items():
        dest = runtime/name; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(signed/name, dest)
        require(sha(dest) == row['sha256'], 'copy changed: '+name)
    changed = copy.deepcopy(profile); changed['files'] = after
    (runtime/'voice-runtime.json').write_text(json.dumps(changed, indent=2)+'\n')
    digest = sha(runtime/'voice-runtime.json'); verify(runtime, digest)
    shutil.copy2(runtime/'resources/settings.json', out/'settings.json')
    bind_carrier(runtime, out/'carrier-build.json', go)
    built = json.loads((out/'carrier-build.json').read_text())
    require(built['sdk_revision'] == old_build['sdk_revision'], 'SDK changed during signing rebind')
    # Source changes cannot disappear behind a signing-only claim.
    source_delta = sorted(n for n in set(built['inputs']) | set(old_build['inputs'])
                          if built['inputs'].get(n) != old_build['inputs'].get(n))
    result = copy.deepcopy(frozen)
    result.update(scope=__doc__, parent_checkpoint=str(parent), parent_freeze_sha256=parent_sha,
        runtime_manifest_sha256=digest, carrier_sha256=built['carrier_sha256'],
        worker_sha256=sha(runtime/'bin/aii_voice_worker.exe'), candidate_execution_validated=False,
        signed=False, installed=False, human_level_qualified=False,
        runtime_authenticode_verified=True, carrier_authenticode_verified=False,
        runtime_bytes=sum(p.stat().st_size for p in runtime.rglob('*') if p.is_file()),
        runtime_files=len(after)+2, settings_sha256=sha(out/'settings.json'),
        authenticode_derivation=dict(report_sha256=report_sha, parent_runtime_sha256=frozen['runtime_manifest_sha256'],
            signed_components=sorted(OWNED), retained_components=sorted(r['path'] for r in report['signed_files'] if r.get('action')=='retained_verified'), source_delta=source_delta, signatures=signatures,
            executable_content_preserved=True, models_changed=False, requalification_required=True))
    for name in result['library_hashes']:
        # Frozen loader observations normalize Windows DLL names; the sealed
        # inventory retains the actual filesystem case (notably DirectML.dll).
        matches=[path for path in after if path.casefold()==('bin/'+name).casefold()]
        require(len(matches)==1, 'ambiguous or missing Windows library: '+name)
        actual=matches[0];library_digest=after[actual]['sha256']
        result['library_hashes'][name] = library_digest
        result['libraries'][name] = dict(source=str(signed/actual), source_sha256=library_digest, relocated_sha256=library_digest)
    with (out/'freeze.json').open('x') as f: json.dump(result, f, indent=2); f.write('\n')
    verify_checkpoint(out)
    require(sha(frozen_path) == parent_sha and sha(report_path) == report_sha, 'parent/receipt changed')
    verify(parent/'runtime', frozen['runtime_manifest_sha256'])
    require(runtime_inventory(signed, target_platform='windows') == after, 'signed source changed during rebuild')
    receipt = dict(passed=True, runtime_manifest_sha256=digest, carrier_sha256=built['carrier_sha256'],
        freeze_sha256=sha(out/'freeze.json'), recipe_sha256=sha(Path(__file__)),
        runtime_authenticode_verified=True, carrier_authenticode_verified=False,
        candidate_execution_validated=False, t3_signed=False, published=False, beta_release_ready=False,
        next_gates=['sign rebuilt carrier', 'requalify final signed bytes', 'pack verified companion',
                    'assemble and T3-sign family', 'fresh installed browser paths', 'publish with authorization'])
    with (out/'rebind-result.json').open('x') as f: json.dump(receipt, f, indent=2); f.write('\n')
    return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for n in ('parent', 'signing-stage', 'out', 'go', 'signtool'):
        p.add_argument('--'+n, type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(prepare(a.parent.resolve(), a.signing_stage.resolve(), a.out.absolute(), a.go, a.signtool), indent=2))
