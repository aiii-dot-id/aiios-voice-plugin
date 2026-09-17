"""Rebinding must neither launder a changed executable nor claim signed readiness."""
import copy
import hashlib
import json
import struct
from pathlib import Path
import pytest

from scripts.rebind_signed_windows_runtime import OWNED, SUBJECT, signing_only_change, validate_report, verify_authenticode


def images(magic=0x20b):
    before=bytearray(513);before[:2]=b'MZ';struct.pack_into('<I',before,60,64)
    before[64:68]=b'PE\0\0';struct.pack_into('<H',before,84,240)
    struct.pack_into('<H',before,88,magic)
    directories=88+(96 if magic==0x10b else 112)
    struct.pack_into('<I',before,directories-4,16)
    before[400:406]=b'CODE!!'
    after=bytearray(before);after.extend(b'\0'*7)
    struct.pack_into('<I',after,88+64,12345)
    struct.pack_into('<II',after,directories+4*8,520,16)
    after.extend(struct.pack('<IHH',16,0x200,2)+b'PKCS7!!!')
    return before,after


@pytest.mark.parametrize('magic',[0x10b,0x20b])
def test_certificate_append_and_checksum_change_preserve_executable(magic):
    signing_only_change(*images(magic))


@pytest.mark.parametrize('damage', ['code','overlay','padding','trailing','header','offset',
    'size','certificate-type','certificate-revision','certificate-length','old-signature','shrink','short'])
def test_signature_does_not_authorize_unrelated_binary_changes(damage):
    before,after=images();directory=88+112+32
    if damage=='code':after[400]^=1
    if damage=='overlay':after[512]^=1
    if damage=='padding':after[514]=1
    if damage=='trailing':after.extend(b'junk')
    if damage=='header':struct.pack_into('<I',after,60,0xffffffff)
    if damage=='offset':struct.pack_into('<I',after,directory,512)
    if damage=='size':struct.pack_into('<I',after,directory+4,8)
    if damage=='certificate-type':struct.pack_into('<H',after,526,1)
    if damage=='certificate-revision':struct.pack_into('<H',after,524,0x100)
    if damage=='certificate-length':struct.pack_into('<I',after,520,100)
    if damage=='old-signature':struct.pack_into('<II',before,directory,512,1)
    if damage=='shrink':after=after[:510]
    if damage=='short':before=b'PE'
    with pytest.raises(ValueError):signing_only_change(before,after)


def report_fixture():
    before={n:dict(sha256='before-'+n,bytes=513,executable=False) for n in OWNED}
    before['bin/vendor.dll']=dict(sha256='unchanged',bytes=100,executable=False)
    after=copy.deepcopy(before)
    for n in OWNED:after[n].update(sha256='after-'+n,bytes=536)
    report=dict(passed=True,parent_runtime_sha256='parent',t3_signed=False,runtime_rebound=False,
                carrier_rebuilt=False,qualified_after_signing=False,signed_files=[])
    for n in sorted(OWNED):report['signed_files'].append(dict(path=n,before_sha256=before[n]['sha256'],
        sha256=after[n]['sha256'],bytes=536,subject=SUBJECT,timestamp_subject='Timestamp authority'))
    return report,before,after


def test_report_binds_exact_eight_owned_files_and_unchanged_vendor_bytes():
    report,before,after=report_fixture();validate_report(report,'parent',before,after)


@pytest.mark.parametrize('damage',['failed','error','parent','missing','duplicate','vendor','extra','hash',
    'before','bytes','subject','timestamp','mode','no-change','false-qualified','false-t3','missing-state'])
def test_incomplete_or_contradictory_signing_cannot_rebind(damage):
    report,before,after=report_fixture();row=report['signed_files'][0];name=row['path']
    if damage=='failed':report['passed']=False
    if damage=='error':report['error']='failed'
    if damage=='parent':report['parent_runtime_sha256']='other'
    if damage=='missing':report['signed_files'].pop()
    if damage=='duplicate':report['signed_files'].append(row.copy())
    if damage=='vendor':after['bin/vendor.dll']['sha256']='replaced'
    if damage=='extra':after['unexpected.dll']={}
    if damage=='hash':row['sha256']='wrong'
    if damage=='before':row['before_sha256']='wrong'
    if damage=='bytes':row['bytes']+=1
    if damage=='subject':row['subject']='CN=someone else'
    if damage=='timestamp':row['timestamp_subject']=''
    if damage=='mode':after[name]['executable']=True
    if damage=='no-change':after[name]['sha256']=row['sha256']=before[name]['sha256']
    if damage=='false-qualified':report['qualified_after_signing']=True
    if damage=='false-t3':report['t3_signed']=True
    if damage=='missing-state':report.pop('runtime_rebound')
    with pytest.raises(ValueError):validate_report(report,'parent',before,after)




def test_signed_looking_report_never_substitutes_for_windows_trust(monkeypatch):
    from scripts import rebind_signed_windows_runtime as module
    # Fake OS responses exercise admission only, not a real signature claim.
    monkeypatch.setattr(module,'os',type('Platform',(),{'name':'nt'}))
    runtime=Path('/stage/runtime');names={'one.exe','two.dll'}
    rows=[dict(path=str(runtime/n),status='Valid',subject=SUBJECT,timestamp='Clock') for n in sorted(names)]
    monkeypatch.setattr(module.subprocess,'check_output',lambda *a,**k:json.dumps(rows).encode())
    calls=[]
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:calls.append((a,k)))
    assert verify_authenticode(runtime,names,Path('/signtool'))==rows and len(calls)==2
    assert all(c[0][0][1:5]==['verify','/pa','/all','/tw'] and c[1]['check'] for c in calls)
    for key,value in [('status','NotSigned'),('subject','Untrusted'),('timestamp',''),('path','/wrong')]:
        old=rows[0][key];rows[0][key]=value
        with pytest.raises(ValueError):verify_authenticode(runtime,names,Path('/signtool'))
        rows[0][key]=old




def test_single_carrier_windows_trust_observation_is_not_mistaken_for_a_list(monkeypatch):
    from scripts import rebind_signed_windows_runtime as module
    monkeypatch.setattr(module,'os',type('Platform',(),{'name':'nt'}))
    root=Path('/carrier');name='aii-voice-t3.exe'
    row=dict(path=str(root/name),status='Valid',subject=SUBJECT,timestamp='Clock')
    monkeypatch.setattr(module.subprocess,'check_output',lambda *a,**k:json.dumps(row).encode())
    calls=[];monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:calls.append(a))
    assert verify_authenticode(root,{name},Path('/signtool'))==[row] and len(calls)==1
    with pytest.raises(ValueError):verify_authenticode(root,{name,'second.exe'},Path('/signtool'))


@pytest.mark.parametrize('damage',[None,'failed','hash','source','timestamp','code','false-qualified'])
def test_adoption_refuses_false_receipts_and_preserves_parent(tmp_path,monkeypatch,damage):
    from scripts import adopt_signed_windows_carrier as module
    from scripts.native_checkpoint_binding import sha
    parent=tmp_path/'parent';signing=tmp_path/'signed';out=tmp_path/'out'
    (parent/'runtime').mkdir(parents=True);signing.mkdir()
    old,new=images();unsigned=parent/'runtime/aii-voice-t3.exe';unsigned.write_bytes(old)
    target=signing/'aii-voice-t3.exe';target.write_bytes(new)
    frozen=dict(carrier_sha256=sha(unsigned),runtime_manifest_sha256='runtime',signed=False)
    build=dict(carrier_sha256=sha(unsigned))
    (parent/'carrier-build.json').write_text(json.dumps(build))
    original=copy.deepcopy((frozen,build,{},{}))
    monkeypatch.setattr(module,'verify_checkpoint',lambda p: copy.deepcopy(original) if p==parent else None)
    calls=[];monkeypatch.setattr(module,'verify_authenticode',lambda *a: calls.append(a) or [{'fixture':True}])
    r=dict(passed=True,source=str(unsigned),target=str(target),before_sha256=sha(unsigned),sha256=sha(target),
        bytes=len(new),subject=SUBJECT,timestamp_subject='Clock',t3_signed=False,execution_requalified=False)
    if damage=='failed':r['passed']=False
    if damage=='hash':r['sha256']='other'
    if damage=='source':r['source']=str(parent/'elsewhere')
    if damage=='timestamp':r['timestamp_subject']=''
    if damage=='false-qualified':r['execution_requalified']=True
    if damage=='code':
        new[400]^=1;target.write_bytes(new);r['sha256']=sha(target)
    (signing/'result.json').write_text(json.dumps(r),encoding='utf-8-sig')
    if damage:
        with pytest.raises(ValueError):module.adopt(parent,signing,out,Path('signtool'))
        assert not out.exists() and not calls
    else:
        result=module.adopt(parent,signing,out,Path('signtool'))
        assert result['passed'] and len(calls)==1 and not result['execution_requalified'] and not result['t3_signed']
        assert sha(out/'runtime/aii-voice-t3.exe')==r['sha256']
    assert unsigned.read_bytes()==old


def test_actual_go_carrier_rebuild_binds_new_inventory_not_parent(tmp_path, monkeypatch):
    # Only the Windows trust response is simulated here. Real files, real PE
    # delta validation and the real pinned Go carrier build execute. These tiny
    # fixtures are NOT inference binaries, real certificates or release evidence.
    from scripts import rebind_signed_windows_runtime as module
    from scripts.build_plugin_carrier import inputs, verify_sdk
    from scripts.package_native_runtime import runtime_inventory
    from scripts.native_checkpoint_binding import sha, verify_checkpoint
    pin,_=verify_sdk();parent=tmp_path/'parent';stage=tmp_path/'stage';out=tmp_path/'candidate'
    original,certificate_fixture=images()
    for directory,raw in [(parent,original),(stage,certificate_fixture)]:
        for name in (*OWNED, 'bin/DirectML.dll'):
            dest=directory/'runtime'/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
        (directory/'runtime/resources').mkdir()
        (directory/'runtime/resources/settings.json').write_text('{}\n')
        (directory/'runtime/aii-voice-t3.exe').write_bytes(b'fixture parent carrier')
    # Vendor bytes never change; lowercase observation still resolves its
    # mixed-case inventory name in the real rebind, not just a helper fixture.
    (stage/'runtime/bin/DirectML.dll').write_bytes(original)
    profile=dict(schema='aiii.voice.native-runtime',qualified=False,platform='windows',arch='amd64',
                 files=runtime_inventory(parent/'runtime',target_platform='windows'))
    for directory in (parent,stage):
        (directory/'runtime/voice-runtime.json').write_text(json.dumps(profile)+'\n')
    binding=sha(parent/'runtime/voice-runtime.json');models=tmp_path/'models';models.mkdir()
    (models/'model').write_bytes(b'unchanged model data')
    old_build=dict(sdk_revision=pin['revision'],inputs=inputs(),runtime_manifest_sha256=binding,
                   carrier_sha256=sha(parent/'runtime/aii-voice-t3.exe'))
    (parent/'carrier-build.json').write_text(json.dumps(old_build))
    frozen=dict(passed=True,signed=False,installed=False,human_level_qualified=False,
        runtime_manifest_sha256=binding,carrier_sha256=old_build['carrier_sha256'],models_root=str(models),
        models={'model':dict(bytes=20,sha256=sha(models/'model'))},
        library_hashes={Path(n).name.lower():profile['files'][n]['sha256'] for n in (*OWNED,'bin/DirectML.dll') if n.endswith('.dll')},libraries={})
    (parent/'freeze.json').write_text(json.dumps(frozen))
    after=runtime_inventory(stage/'runtime',target_platform='windows')
    report=dict(passed=True,parent_runtime_sha256=binding,t3_signed=False,runtime_rebound=False,
                carrier_rebuilt=False,qualified_after_signing=False,signed_files=[])
    for n in sorted(OWNED):report['signed_files'].append(dict(path=n,before_sha256=profile['files'][n]['sha256'],
        sha256=after[n]['sha256'],bytes=after[n]['bytes'],subject=SUBJECT,timestamp_subject='fixture'))
    # Match the production PowerShell receipt, including its UTF-8 BOM.
    (stage/'result.json').write_text(json.dumps(report),encoding='utf-8-sig')
    observations=[]
    def simulated_trust(runtime,names,tool):
        assert runtime==stage/'runtime' and names==OWNED
        observations.append('verified-before-output')
        assert not out.exists()
        return [{'test_fixture_only':True}]
    monkeypatch.setattr(module,'verify_authenticode',simulated_trust)
    result=module.prepare(parent,stage,out,Path('/usr/local/go1.27/bin/go'),Path('fixture-signtool'))
    assert observations==['verified-before-output'] and result['passed']
    current,build,_,_=verify_checkpoint(out)
    assert result['runtime_manifest_sha256']!=binding
    assert '-X main.packagedRuntimeSHA='+result['runtime_manifest_sha256'] in build['command']
    assert current['models']==frozen['models'] and current['models_root']==frozen['models_root']
    assert current['runtime_files']==len(after)+2
    assert current['worker_sha256']==after['bin/aii_voice_worker.exe']['sha256']
    assert current['authenticode_derivation']['source_delta']==[]
    assert not result['carrier_authenticode_verified'] and not result['candidate_execution_validated']
    assert not result['t3_signed'] and not result['published'] and not result['beta_release_ready']
    assert sha(parent/'runtime/voice-runtime.json')==binding
    with pytest.raises(ValueError,match='fresh output'):module.prepare(parent,stage,out,Path('unused'),Path('unused'))
