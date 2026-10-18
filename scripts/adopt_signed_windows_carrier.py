"""Bind the signed carrier without altering its unsigned parent or claiming T3."""
import argparse
import copy
import json
from pathlib import Path
import shutil

from scripts.native_checkpoint_binding import sha, verify_checkpoint
from scripts.rebind_signed_windows_runtime import require, signing_only_change, verify_authenticode, SUBJECT


def adopt(parent, signing, out, signtool):
    require(not out.exists(), 'fresh output required')
    before=verify_checkpoint(parent);frozen,built=copy.deepcopy(before[0]),copy.deepcopy(before[1])
    raw=(signing/'result.json').read_bytes();r=json.loads(raw.decode('utf-8-sig'))
    unsigned=parent/'runtime/aii-voice-t3.exe';signed=signing/'aii-voice-t3.exe'
    require(r['passed'] and not r.get('error') and not r['t3_signed'] and not r['execution_requalified'],'incomplete carrier signing')
    require(Path(r['source']).resolve()==unsigned.resolve() and Path(r['target']).resolve()==signed.resolve(),'wrong carrier paths')
    require(r['before_sha256']==sha(unsigned)==frozen['carrier_sha256'],'unsigned carrier binding differs')
    require(r['sha256']==sha(signed) and r['bytes']==signed.stat().st_size,'signed carrier binding differs')
    require(r['subject']==SUBJECT and r['timestamp_subject'],'publisher or timestamp differs')
    signing_only_change(unsigned.read_bytes(),signed.read_bytes())
    signatures=verify_authenticode(signing,{'aii-voice-t3.exe'},signtool)
    require((signing/'result.json').read_bytes()==raw and sha(signed)==r['sha256'],'signing stage changed')
    shutil.copytree(parent,out)
    shutil.copy2(signed,out/'runtime/aii-voice-t3.exe')
    built.update(carrier_sha256=r['sha256'],carrier_bytes=r['bytes'],
        authenticode_derivation=dict(unsigned_build_record_sha256=sha(parent/'carrier-build.json'),
            unsigned_carrier_sha256=r['before_sha256'],report_sha256=sha(signing/'result.json'),signatures=signatures))
    (out/'carrier-build.json').write_text(json.dumps(built,indent=2)+'\n')
    frozen.update(carrier_sha256=r['sha256'],carrier_authenticode_verified=True,
        candidate_execution_validated=False,signed=False,
        runtime_bytes=sum(p.stat().st_size for p in (out/'runtime').rglob('*') if p.is_file()))
    (out/'freeze.json').write_text(json.dumps(frozen,indent=2)+'\n')
    verify_checkpoint(out);require(verify_checkpoint(parent)==before,'parent changed')
    result=dict(passed=True,checkpoint=str(out),freeze_sha256=sha(out/'freeze.json'),
        carrier_sha256=r['sha256'],runtime_manifest_sha256=frozen['runtime_manifest_sha256'],
        executable_content_preserved=True,runtime_authenticode_verified=True,carrier_authenticode_verified=True,
        execution_requalified=False,t3_signed=False,beta_release_ready=False)
    with (out/'carrier-adoption.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('parent','signing','out','signtool'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();print(json.dumps(adopt(a.parent.resolve(),a.signing.resolve(),a.out.resolve(),a.signtool)))
