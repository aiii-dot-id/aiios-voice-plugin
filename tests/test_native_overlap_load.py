import copy
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile
import pytest
from scripts.stage_native_overlap_load import candidate_models,candidate_api,ROOT
from scripts.audit_native_overlap_load import validate_profiles,validate_source

def test_actual_source_delta_and_publication():
    path=ROOT/'deliverables/native-tts-composition-pair-windows-20260913-r2/transfer/source.zip'
    with zipfile.ZipFile(path) as z:
        models=z.read('runtime/native/session/native_models.cpp');api=z.read('runtime/native/session/native_c_api.cpp')
    files={'original/native_models.cpp':models,'original/native_c_api.cpp':api,
      'runtime/native/session/native_models.cpp':candidate_models(models),'runtime/native/session/native_c_api.cpp':candidate_api(api)}
    files['overlap-contract.json']=json.dumps({'changed_originals':{'runtime/native/session/'+n:hashlib.sha256(raw).hexdigest() for n,raw in [('native_models.cpp',models),('native_c_api.cpp',api)]}}).encode()
    validate_source(files.__getitem__,{})
    for name,old,new in [('runtime/native/session/native_models.cpp',b'vad(p)',b'vad(ModelPaths{})'),
                         ('runtime/native/session/native_c_api.cpp',b'owner->models.recognizer();',b'/* no join */')]:
        bad=dict(files);bad[name]=bad[name].replace(old,new)
        with pytest.raises(AssertionError):validate_source(bad.__getitem__,{})

def test_profile_does_not_smuggle_a_new_inference_library():
    a={'backend':'vulkan','worker':'a/worker','model_paths':['same'],'libraries':{'aii_voice_runtime.dll':'old','tts.dll':'tts','asr.dll':'asr'}}
    b=copy.deepcopy(a);b['worker']='b/worker';b['libraries']['aii_voice_runtime.dll']='new';validate_profiles(a,b)
    for key,value in [('asr.dll','new-asr'),('tts.dll','new-tts')]:
        bad=copy.deepcopy(b);bad['libraries'][key]=value
        with pytest.raises(AssertionError):validate_profiles(a,bad)

def test_real_cpp_owner_and_compiling_deferred_mutation(tmp_path):
    folder=ROOT/'runtime/native/session'
    for mutation in (False,True):
        target=tmp_path/('mutant' if mutation else 'original');target.mkdir()
        raw=(folder/'pending_model.h').read_text()
        if mutation:raw=raw.replace('std::launch::async','std::launch::deferred')
        (target/'pending_model.h').write_text(raw)
        (target/'test.cpp').write_bytes((folder/'pending_model_test.cpp').read_bytes())
        binary=target/'probe'
        subprocess.run(['c++','-std=c++17','-pthread','-Wall','-Wextra','-Werror',str(target/'test.cpp'),'-o',str(binary)],check=True,timeout=30)
        run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
        assert (run.returncode!=0)==mutation,run.stdout+run.stderr
        if mutation:assert 'construction was deferred' in run.stderr

def test_actual_model_load_api_waits_and_cleans_up_with_compiling_mutation(tmp_path):
    source=ROOT/'deliverables/native-overlap-load-windows-20260913-r1/transfer/source.zip'
    with zipfile.ZipFile(source) as z:
        for n in z.namelist():
            if n.startswith('runtime/') and n.endswith(('.h','.cpp')):
                p=tmp_path/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(z.read(n))
    folder=tmp_path/'runtime/native/session'
    (folder/'overlap_load_api_test.cpp').write_bytes((ROOT/'runtime/native/session/overlap_load_api_test.cpp').read_bytes())
    original=(folder/'native_c_api.cpp').read_text()
    for mutation in (False,True):
        (folder/'native_c_api.cpp').write_text(original.replace('owner->models.recognizer();','/* join deliberately omitted */') if mutation else original)
        binary=tmp_path/('mutated-api' if mutation else 'load-api')
        subprocess.run(['c++','-std=c++17','-pthread','-Wall','-Wextra','-Werror',
            *[str(folder/n) for n in ('native_models.cpp','native_c_api.cpp','overlap_load_api_test.cpp')],'-o',str(binary)],check=True,timeout=60)
        for scenario in (('success',) if mutation else ('success','asr-failure','tts-failure')):
            result=subprocess.run([str(binary),scenario,str(tmp_path/'model.f32')],capture_output=True,text=True,timeout=10)
            assert (result.returncode!=0)==mutation,result.stdout+result.stderr
            if mutation:assert 'model handle published before ASR' in result.stderr
