import hashlib
import io
import json
import tarfile

import pytest
from scripts.stage_qualified_runtime import audit_binding, check_archive, sha


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
