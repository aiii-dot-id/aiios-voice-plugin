import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile
import shutil
import os

import pytest

from scripts.prepare_native_desktop_packages import checked_parent
from scripts.audit_native_desktop_packages import operation_schemas

ROOT=Path(__file__).resolve().parents[1]
PARENT=ROOT/'deliverables/native-shared-scratch-runtime-windows-20260914-r1'

@pytest.fixture(scope='module')
def schema_probe(tmp_path_factory):
    out=tmp_path_factory.mktemp('actual-host-schema')
    source=ROOT/'.build/aii-os-67e0778-browser-r1/internal/jsonschema/jsonschema.go'
    assert hashlib.sha256(source.read_bytes()).hexdigest()=='fee077a20ead00e864c7b7239fcb0157b7072279c006092b98c301e6c57263c8'
    target=out/'internal/jsonschema';target.mkdir(parents=True)
    shutil.copyfile(source,target/'jsonschema.go')
    shutil.copyfile(ROOT/'scripts/probe_speaker_output_schema.go',out/'main.go')
    (out/'go.mod').write_text('module schema-probe\n\ngo 1.27.0\n')
    subprocess.run(['/usr/local/go1.27/bin/go','build','-trimpath','-buildvcs=false','-o',str(out/'probe'),'.'],
                   cwd=out,env={**os.environ,'GOWORK':'off','GOTOOLCHAIN':'local','GOPROXY':'off'},
                   capture_output=True,check=True,timeout=30)
    return out/'probe'

@pytest.mark.parametrize('damage',('none','envelope','missing_session','permission_claim','wrong_revision'))
def test_corrected_output_schema_matches_actual_recorded_operation_results(schema_probe,damage):
    with zipfile.ZipFile(PARENT/'windows-evidence.zip') as z:
        record=json.loads(z.read('run/sdk/result.json'))
    replies=[x['reply']['result'] for x in record['cases']
             if x['reply'].get('result',{}).get('status')=='succeeded']
    values=[copy.deepcopy(x['operation_result']) for x in replies]
    assert len(values)>=5 and any(x['speakers'] for x in values)
    schema=json.loads((ROOT/'plugin/native/schemas/speaker.output.json').read_text())
    if damage=='envelope':values=replies
    elif damage=='missing_session':del values[0]['session_id']
    elif damage=='permission_claim':values[0]['used_for_permissions']=True
    elif damage=='wrong_revision':values[0]['revision']=0
    p=subprocess.run([str(schema_probe)],input=json.dumps({'schema':schema,'values':values}),
                     text=True,capture_output=True,timeout=10)
    if damage=='none':assert p.returncode==0,p.stderr
    else:assert p.returncode!=0 and 'value 0:' in p.stderr

@pytest.fixture(scope='module')
def staged(tmp_path_factory):
    out=tmp_path_factory.mktemp('qualified-package')/'source'
    p=subprocess.run([sys.executable,'-m','scripts.stage_native_qualified_package','--out',str(out)],
                     cwd=ROOT,capture_output=True,text=True,timeout=30)
    assert p.returncode==0,p.stderr
    with zipfile.ZipFile(out/'source.zip') as z:
        return {n:z.read(n) for n in z.namelist()}


def test_stage_preserves_native_and_proof_bytes_and_ships_schema_aware_assembler(staged):
    with zipfile.ZipFile(PARENT/'transfer/source.zip') as z:
        for n in z.namelist():
            if n.startswith(('plugin/native/','runtime/native/')) or n in (
                'scripts/prove_native_session_enrollment.py','scripts/prove_native_operator_settings.py',
                'scripts/native_checkpoint_binding.py'):
                assert staged[n]==z.read(n),n
    assert b'must(addSchemas(files, desc, root))' in staged['scripts/private_cp1_package.go']
    assert b"new runtime requires a fresh Windows memory observation" in staged['scripts/prepare_native_desktop_packages.py']
    assert 'test-wheels/psutil-7.1.0-cp37-abi3-win_amd64.whl' in staged
    spec=json.loads(staged['packaging-parent.json'])
    assert spec['version']=='0.1.0-native-cp3-windows2'
    assert len(spec['package_schemas'])==5
    for n,h in spec['package_schemas'].items():
        assert hashlib.sha256(staged['package-schemas/'+n]).hexdigest()==h
        assert staged['package-schemas/'+n]==(ROOT/'plugin/native'/n).read_bytes()


@pytest.mark.parametrize('damage',('none','source','complete','runtime','carrier','tts','version'))
def test_packaging_cannot_attach_to_an_unqualified_or_different_runtime(staged,damage):
    spec=json.loads(staged['packaging-parent.json'])
    data={'source.zip':(PARENT/'transfer/source.zip').read_bytes(),
          'evidence.zip':(PARENT/'windows-evidence.zip').read_bytes()}
    with zipfile.ZipFile(PARENT/'windows-evidence.zip') as z:
        data.update({n:z.read(n) for n in spec['files'] if n.startswith('run/')})
    checked_parent(spec,data.__getitem__)
    if damage=='none':return
    if damage=='source':data['source.zip']+=b'drift'
    elif damage=='version':spec['version']='release-not-tested'
    elif damage=='complete':
        row=json.loads(data['run/complete.json']);row['passed']=False
        data['run/complete.json']=json.dumps(row).encode()
        spec['files']['run/complete.json']=hashlib.sha256(data['run/complete.json']).hexdigest()
    else:spec[{'runtime':'runtime_manifest_sha256','carrier':'carrier_sha256','tts':'tts_sha256'}[damage]]='0'*64
    with pytest.raises(AssertionError):checked_parent(spec,data.__getitem__)


def schemas():
    refs={n.name:n.read_bytes() for n in (ROOT/'plugin/native/schemas').glob('*.json')}
    install={'schemas/'+n:b for n,b in refs.items()}
    descriptors=[{'id':'speaker.'+op,'input':'schemas/speaker-'+op+'.input.json','output':'schemas/speaker.output.json'}
                 for op in ('enroll','list','remove','reset')]
    return install,descriptors


@pytest.mark.parametrize('damage',('none','missing','outside','nonobject','broken_json','wrong_output'))
def test_missing_or_invalid_referenced_schema_cannot_pass_package_audit(damage):
    install,descriptors=schemas()
    assert len(operation_schemas(install,descriptors))==5
    if damage=='none':return
    if damage=='missing':del install[descriptors[0]['input']]
    elif damage=='outside':descriptors[0]['input']='schemas/../other.json'
    elif damage=='nonobject':install[descriptors[0]['input']]=b'false'
    elif damage=='broken_json':install[descriptors[0]['input']]=b'{'
    else:descriptors[0]['output']='schemas/other-output.json'
    with pytest.raises((AssertionError,ValueError)):operation_schemas(install,descriptors)
