"""Byte-bound Intel NuGet redistribution notices for the unchanged Windows DLL.

No library is loaded, installed, replaced or relicensed. Intel supplies the
same bytes through this channel under the packaged Simplified Software License.
"""
import hashlib
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT/'artifacts/intel-openmp-redist-20260917-r1/intelopenmp.redist.win.2025.2.0.756.nupkg'
PACKAGE_SHA = '3e134f621d5bccb4c134c97b7c3ce22c8efff51550dcbd9ab87e1b73a19f3ab1'
URL = 'https://api.nuget.org/v3-flatcontainer/intelopenmp.redist.win/2025.2.0.756/intelopenmp.redist.win.2025.2.0.756.nupkg'
DLL = 'runtimes/win-x64/native/libiomp5md.dll'
DLL_SHA = 'd9d66ed25f1a0ea725fa3a41b22cfd5d182c19dbe4771d9c90ca02ad7466f6a1'
DLL_SIZE = 1613680
NOTICES = {
    'license.txt': '6c00ae54b2a610ea009e0f5c5d3aa79023eb694c7fb72857654c53c6e99d7c97',
    'share/doc/compiler/licensing/openmp/third-party-programs.txt': 'f8ce918fe7311ce279e68380a2e233f8a42b1cd3dda8f4c48d6de97a0255c1d7',
    'intelopenmp.redist.win.nuspec': '4375832096232fbf116d2983ac7f946af3654da574f20f62b0b3ade176acc7a7',
}


def verified_notices(shipped, package=PACKAGE):
    if (shipped.get('sha256'),shipped.get('bytes')) != (DLL_SHA,DLL_SIZE):
        raise ValueError('shipped Intel DLL differs from the redistributable')
    raw = package.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PACKAGE_SHA:
        raise ValueError('pinned Intel NuGet package differs')
    files = {}
    with zipfile.ZipFile(package) as z:
        if len(set(z.namelist()))!=len(z.namelist()): raise ValueError('duplicate distribution member')
        binary = z.read(DLL)
        if len(binary)!=DLL_SIZE or hashlib.sha256(binary).hexdigest()!=DLL_SHA:
            raise ValueError('Intel distribution DLL differs')
        for name,digest in NOTICES.items():
            value = z.read(name)
            if hashlib.sha256(value).hexdigest()!=digest:
                raise ValueError('Intel distribution notice differs')
            files['notices/intel-openmp-redist-2025.2.0.756/'+Path(name).name] = value
    binding = dict(component='libiomp5md.dll',platform='windows',
        distribution='Intel NuGet intelopenmp.redist.win 2025.2.0.756',
        source_url=URL,package_sha256=PACKAGE_SHA,package_member=DLL,
        source_sha256=DLL_SHA,shipped_sha256=DLL_SHA,bytes=DLL_SIZE,
        declared_terms='Intel Simplified Software License (October 2022)',
        notice_directory='notices/intel-openmp-redist-2025.2.0.756',
        proof='Unmodified complete DLL matches Intel redistribution channel; all packaged notices retained',
        third_party_terms_unchanged=True)
    return files,binding


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--runtime-sha256',required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    def digest(path):
        with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    before=digest(a.runtime)
    if before!=a.runtime_sha256:raise ValueError('runtime archive hash differs')
    with tarfile.open(a.runtime) as t:
        members=[m for m in t if m.name=='runtime/bin/libiomp5md.dll']
        if len(members)!=1 or not members[0].isfile():raise ValueError('one runtime DLL required')
        dll=t.extractfile(members[0]).read()
    files,binding=verified_notices(dict(sha256=hashlib.sha256(dll).hexdigest(),bytes=len(dll)))
    a.out.mkdir(parents=True,exist_ok=False)
    rows={}
    for name,raw in files.items():
        path=a.out/name;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as stream:stream.write(raw)
        rows[name]=dict(sha256=digest(path),bytes=len(raw))
    if digest(a.runtime)!=before:raise ValueError('runtime changed during readback')
    result=dict(observed_utc=datetime.now(timezone.utc).isoformat(),byte_match=True,
        binding=binding,runtime_archive_sha256=before,notices=rows,source_sha256=digest(Path(__file__)),
        distribution_role='Intel-designated redistributable channel; retain its license and all third-party notices',
        previous_signed_package_changed=False,installed=False,published=False,
        remaining='Include these exact notices in the next signed package. Overall release acceptance is separate.')
    (a.out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
