import hashlib
import io
import json
import tarfile

import pytest
from scripts.stage_qualified_runtime import audit_binding, audit_checkpoint, check_archive, sha, windows_signatures


def fixture(path, damage=None):
    pcm=b'native-binary'; digest=hashlib.sha256(pcm).hexdigest()
    rows={'bin/worker':dict(bytes=len(pcm),sha256=digest,executable=True)}
    raw=json.dumps({'files':[dict(path='bin/worker',size=len(pcm),sha256='sha256:'+digest,mode='exec')]}).encode()
    with tarfile.open(path,'w:gz') as t:
        for n in ('runtime/','runtime/bin/'):
            i=tarfile.TarInfo(n);i.type=tarfile.DIRTYPE;i.mode=0o755;t.addfile(i)
        for n,data,mode in (('runtime/inventory.json',raw,0o644),('runtime/bin/worker',pcm,0o755)):
            i=tarfile.TarInfo(n);i.size=len(data);i.mode=mode
            if damage=='mode' and n.endswith('worker'):i.mode=0o644
            if damage=='bytes' and n.endswith('worker'):data=b'X'+data[1:]
            t.addfile(i,io.BytesIO(data))
            if damage=='duplicate' and n.endswith('worker'):t.addfile(i,io.BytesIO(data))
        if damage in ('symlink','extra','traversal'):
            i=tarfile.TarInfo('runtime/extra' if damage!='traversal' else '../escape')
            if damage=='symlink':i.type=tarfile.SYMTYPE;i.linkname='/etc/passwd'
            t.addfile(i,io.BytesIO(b''))
    declaration=dict(sha256=sha(path),size=path.stat().st_size,inventory_sha256=hashlib.sha256(raw).hexdigest(),files=1,installed_bytes=len(pcm))
    if damage=='budget':declaration['installed_bytes']=0
    return declaration,rows


def test_sdk_directory_entries_and_exact_files_are_accepted(tmp_path):
    path=tmp_path/'runtime.tar.gz';declaration,rows=fixture(path)
    check_archive(path,declaration,rows)


@pytest.mark.parametrize('damage',['mode','bytes','duplicate','symlink','extra','traversal','budget'])
def test_bad_archive_refused_even_with_recomputed_archive_digest(tmp_path,damage):
    path=tmp_path/'runtime.tar.gz';declaration,rows=fixture(path,damage)
    with pytest.raises(ValueError):check_archive(path,declaration,rows)


def test_evidence_must_name_exact_runtime(tmp_path):
    p=tmp_path/'audit.json';p.write_text(json.dumps(dict(passed=True,runtime_manifest_sha256='one')))
    audit_binding(p,sha(p),'one')
    with pytest.raises(ValueError):audit_binding(p,sha(p),'two')
    with pytest.raises(ValueError):audit_binding(p,'wrong','one')


def bound_checkpoint(root):
    prefix='C:\\qualification\\signed-checkpoint'
    bound={}
    for name in ('freeze.json','carrier-build.json','runtime/voice-runtime.json','runtime/aii-voice-t3.exe'):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(name.encode())
        bound[prefix+'\\'+name.replace('/','\\')]=sha(path)
    return dict(checkpoint=prefix,bindings=bound)


def test_copied_windows_proof_binds_exact_checkpoint(tmp_path):
    proof=bound_checkpoint(tmp_path)
    audit_checkpoint(proof,tmp_path,'aii-voice-t3.exe')


@pytest.mark.parametrize('name',['freeze.json','carrier-build.json','runtime/voice-runtime.json','runtime/aii-voice-t3.exe'])
def test_modified_checkpoint_or_carrier_is_not_qualified(tmp_path,name):
    proof=bound_checkpoint(tmp_path);(tmp_path/name).write_bytes(b'changed')
    with pytest.raises(ValueError,match='binding differs'):audit_checkpoint(proof,tmp_path,'aii-voice-t3.exe')


def test_evidence_from_another_checkpoint_cannot_match_by_basename(tmp_path):
    proof=bound_checkpoint(tmp_path);proof['checkpoint']='C:\\other'
    with pytest.raises(ValueError,match='binding differs'):audit_checkpoint(proof,tmp_path,'aii-voice-t3.exe')


def test_windows_cannot_skip_native_signature_verification(tmp_path):
    with pytest.raises(ValueError,match='native Authenticode'):
        windows_signatures(tmp_path,dict(platform='windows'),None)


def test_non_windows_cannot_claim_authenticode(tmp_path):
    assert windows_signatures(tmp_path,dict(platform='darwin'),None)==[]
    with pytest.raises(ValueError,match='non-Windows'):
        windows_signatures(tmp_path,dict(platform='darwin'),tmp_path/'signtool')


def test_windows_checks_every_owned_image_and_carrier(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from scripts import stage_qualified_runtime as stage
    monkeypatch.setattr(stage,'os',SimpleNamespace(name='nt'))
    calls=[]
    def verify(root,names,tool):
        calls.append((root,names,tool));return ['observed']
    monkeypatch.setattr(stage,'verify_authenticode',verify)
    profile=dict(platform='windows',files=dict.fromkeys(stage.OWNED))
    assert stage.windows_signatures(tmp_path,profile,tmp_path/'signtool')==['observed']
    assert calls==[(tmp_path,stage.OWNED|{'aii-voice-t3.exe'},tmp_path/'signtool')]
    def refuse(*args):raise ValueError('OS rejected signature')
    monkeypatch.setattr(stage,'verify_authenticode',refuse)
    with pytest.raises(ValueError,match='OS rejected'):stage.windows_signatures(tmp_path,profile,tmp_path/'signtool')
