import importlib.util
from pathlib import Path
import zipfile
import pytest

ROOT=Path(__file__).resolve().parents[1]


def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def test_candidate_changes_only_explicit_backend_admission():
    mod=load('stage_native_cuda_speech')
    with zipfile.ZipFile(ROOT/'deliverables/native-tts-owned-retirement-windows-20260913-r1/transfer/source.zip') as z:
        before=z.read('source/original-resident.cpp')
    after=mod.cuda_resident(before)
    assert b'selected != "cuda"' in after
    assert b'if (selected == "cuda") options.backend.type = engine::core::BackendType::Cuda;' in after
    for symbol in (b'NV_EXPORT int nv_start',b'NV_EXPORT int nv_cancel',b'NV_EXPORT int nv_next'):
        assert before[before.index(symbol):]==after[after.index(symbol):]
    with pytest.raises(ValueError):mod.cuda_resident(before+b'\n')


def test_waveform_cannot_hide_truncation_or_changed_prefix():
    mod=load('build_native_cuda_speech_windows')
    a=[0.2,-0.1,0.6,-0.4]
    exact=mod.waveform(a,a)
    assert exact['nrmse']==0 and exact['correlation']==pytest.approx(1)
    assert mod.waveform(a,a[:-1])['passed'] is False
    wrong=mod.waveform(a,[-x for x in a])
    assert wrong['nrmse']>0.05 and wrong['correlation']<0.998


def test_build_is_cuda_only_and_compiles_only_the_selected_model():
    s=(ROOT/'runtime/native_pocket/windows_cuda/CMakeLists.txt').read_text()
    assert 'STREQUAL "61-real"' in s
    for declaration in ['set(ENGINE_ENABLE_CUDA ON','set(ENGINE_ENABLE_VULKAN OFF','set(AUDIOCPP_MODEL_SET custom','set(AUDIOCPP_MODELS pocket_tts','set(GGML_BACKEND_DL OFF']:
        assert declaration in s


def test_complete_qualified_numerical_delta_is_bound_to_original_archive():
    import json,hashlib,tarfile
    with zipfile.ZipFile(ROOT/'deliverables/native-cuda-speech-windows-20260913-r2/delta.zip') as z,tarfile.open(ROOT/'artifacts/native-pocket-source-20260910-r2/source.tar.gz') as tar:
        delta=json.loads(z.read('manifest.json'));assert len(delta)==6
        for n,row in delta.items():
            original=tar.extractfile('audio.cpp-3174e6b26f11a0e39b4f150961dce98f43ba860d/'+n).read()
            assert hashlib.sha256(original).hexdigest()==row['before_sha256']
            assert hashlib.sha256(z.read(n)).hexdigest()==row['sha256']
        assert b'GeluApproximation::Tanh' in z.read('src/models/pocket_tts/graph_common.h')
    builder=(ROOT/'scripts/build_native_cuda_speech_windows.py').read_text()
    assert '-DCMAKE_C_FLAGS_INIT=-DGGML_GELU_FORCE_F32' in builder
    assert '-DCMAKE_CXX_FLAGS_INIT=-DGGML_GELU_FORCE_F32' in builder


def test_speed_cannot_compensate_for_noisy_baseline_or_slow_recovery():
    # Import through the package so the auditor's sibling import is resolved.
    import sys
    sys.path.insert(0,str(ROOT))
    from scripts.audit_native_cuda_speech import performance
    gate={'first_pcm_ratio_max':0.9,'all_other_time_ratios_max':1.05,'rtf_max':1.0,'baseline_first_pcm_spread_max':1.05}
    runs=[]
    for backend in ['vulkan','cuda','cuda','vulkan','vulkan','cuda']:
        factor=0.8 if backend=='cuda' else 1.0
        rows=[{'seconds':factor}]+[{'seconds':factor,'first_pcm_seconds':factor/2,'samples':48000} for _ in range(5)]
        runs.append({'backend':backend,'rows':rows})
    assert performance(runs,gate)['passed']
    runs[3]['rows'][1]['first_pcm_seconds']*=1.5
    assert not performance(runs,gate)['passed']
    runs[3]['rows'][1]['first_pcm_seconds']/=1.5
    for r in runs:
        if r['backend']=='cuda':r['rows'][4]['seconds']=1.2
    assert not performance(runs,gate)['passed']


def test_incremental_continuation_cannot_retry_a_speech_failure():
    mod=load('build_native_cuda_speech_windows')
    from copy import deepcopy
    previous={'passed':False,'runs':[],'error':'TimeoutExpired(cmd, 1200)',
              'stages':[{'name':'build','retired':True}]}
    mod.require_build_timeout(previous)
    for key,value in [('passed',True),('runs',[{'backend':'cuda'}]),
                      ('error','AssertionError(waveform)'),('stages',[{'name':'build','retired':False}])]:
        bad=deepcopy(previous);bad[key]=value
        with pytest.raises(ValueError):mod.require_build_timeout(bad)


def test_incremental_build_keeps_the_frozen_speech_proof_exact():
    with zipfile.ZipFile(ROOT/'deliverables/native-cuda-speech-windows-20260913-r2/transfer/source.zip') as z:
        before=z.read('scripts/qualify_native_candidate_windows.py')
    after=(ROOT/'scripts/build_native_cuda_speech_windows.py').read_bytes()
    seam=b'        binary=bind(build/'
    assert before[before.index(seam):]==after[after.index(seam):]


def test_precision_trial_reuses_only_a_finished_failed_candidate():
    from copy import deepcopy
    mod=load('build_native_cuda_speech_windows')
    previous={'passed':False,'error':"AssertionError('CUDA waveform parity gate failed')",
              'runs':[{'backend':'vulkan'}],
              'stages':[{'name':'build','retired':True,'exit_code':0},
                        {'name':'2-cuda','retired':True,'exit_code':0}]}
    mod.require_waveform_failure(previous)
    for key,value in [('passed',True),('runs',[]),('error','TimeoutExpired(cmd,1200)'),
                      ('stages',[{'name':'2-cuda','retired':False,'exit_code':0}]),
                      ('stages',[{'name':'2-cuda','retired':True,'exit_code':1}])]:
        bad=deepcopy(previous);bad[key]=value
        with pytest.raises(ValueError):mod.require_waveform_failure(bad)


def test_relink_reads_one_exact_target_and_never_drops_an_object():
    mod=load('build_native_cuda_speech_windows')
    prefix='CMakeFiles\\native_pocket_resident.dir\\source\\'
    objects=[prefix+'mimi_decoder.cpp.obj']+[prefix+f'other{i}.cpp.obj' for i in range(8)]
    compile_line='build '+objects[0]+': CXX_COMPILER input.cpp\n'
    text=(compile_line+'  DEFINES = -DGGML_USE_CUDA\n  FLAGS = /O2 /MD\n  INCLUDES = -IC:\\source\n'+'\n'*12
          +'build native_pocket_resident.dll native_pocket_resident.lib: LINKER '+' '.join(objects)
          +' | engine\\engine_runtime.lib\n  LINK_LIBRARIES = engine\\engine_runtime.lib kernel32.lib\n')
    recipe=mod.relink_recipe(text)
    assert recipe['objects']==objects and recipe['target']==objects[0]
    assert recipe['defines']==['-DGGML_USE_CUDA'] and recipe['flags']==['/O2','/MD']
    for bad in (text+compile_line,text.replace(' '+objects[-1]+' |',' |')):
        with pytest.raises(ValueError):mod.relink_recipe(bad)
