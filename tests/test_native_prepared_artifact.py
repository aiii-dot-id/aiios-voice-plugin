import hashlib
import json
from pathlib import Path
import subprocess
import zipfile
import pytest
from scripts.stage_native_prepared_artifact import ROOT,transform,runner
from scripts.audit_native_prepared_artifact import validate_source

def test_source_changes_only_the_encoder_artifact():
    original=(ROOT/'runtime/native_asr/asr.cpp').read_bytes()
    candidate=transform(original)
    assert b'initializers->' not in candidate
    assert candidate.count(b'prepared->check_unchanged();')==1
    assert b'options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);' in candidate
    for label in ('decoder_mapping_and_hash','joiner_mapping_and_hash','vocabulary_mapping_and_hash','geometry_and_vocabulary_validation'):
        assert original.count(label.encode())==candidate.count(label.encode())==1
    with pytest.raises(ValueError):transform(original.replace(b'initializers->attach(encoder_options);',b'changed();'))

def test_complete_acoustic_commands_survive_artifact_owner_derivation():
    original=(ROOT/'scripts/compare_native_precomputed_windows.py').read_bytes()
    candidate=runner(original).decode()
    start=original.decode().index("        expected = json.loads((a.source / 'reference.json').read_text())")
    assert original.decode()[start:]==candidate[candidate.index("        expected = json.loads((a.source / 'reference.json').read_text())"):]
    assert "if candidate_kind=='precomputed'" not in candidate
    assert "source_checkpoint_runtime_dependency']=False" in candidate
    assert "os.link(src,overlay/src.name)" in candidate

def test_real_native_prepared_binding_and_compiling_hash_mutation(tmp_path):
    ort=ROOT/'.build/native-asr-cpp-20260911-r1/lib/libonnxruntime.1.dylib'
    includes=ROOT/'.build/ios-wespeaker-device/Frameworks/onnxruntime.xcframework/macos-arm64_x86_64/onnxruntime.framework/Versions/A/Headers'
    for mutation in (False,True):
        folder=tmp_path/('mutant' if mutation else 'original')
        for base in ('runtime/native_asr','runtime/native/platform','runtime/native/vendor'):
            for src in (ROOT/base).rglob('*'):
                if src.is_file() and src.suffix in ('.h','.cpp','.c'):
                    dest=folder/src.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(src.read_bytes())
        path=folder/'runtime/native/platform/readonly_model.cpp'
        if mutation:
            raw=path.read_text();assert raw.count('if (sha256(s.view, s.size) != digest)')==1
            path.write_text(raw.replace('if (sha256(s.view, s.size) != digest)','if (false && sha256(s.view, s.size) != digest)'))
        exe=folder/'probe'
        subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-ffp-contract=off','-I',str(includes),
            str(folder/'runtime/native_asr/prepared_encoder_test.cpp'),str(path),str(ort),
            '-Wl,-rpath,'+str(ort.parent),'-o',str(exe)],check=True,timeout=60)
        p=subprocess.run([str(exe),str(folder/'fixture')],capture_output=True,text=True,timeout=20)
        assert (p.returncode!=0)==mutation,p.stdout+p.stderr
        if mutation:assert 'corrupt prepared weights accepted' in p.stderr
        else:assert 'exact inference after mappings retire' in p.stdout

def test_offline_derivation_matches_prepared_artifact_binding():
    path=ROOT/'deliverables/native-streaming-20260909/preoptimized-stt-20260910-r3/evidence/export/export.json'
    r=json.loads(path.read_text());assert r['passed'] and r['model_identity']['verified_encoder_tensors']==640
    assert r['model_identity']['checkpoint_sha256']=='9eebdd6590289cb3030f310858f3df93256600a800a3e8200c5993d5f967e174'
    header=(ROOT/'runtime/native_asr/prepared_encoder.h').read_text()
    for row in r['exported'].values():assert row['sha256'] in header

@pytest.mark.parametrize('damage',[None,'hash-check','threads','other-file'])
def test_independent_source_audit_refuses_unrelated_or_missing_integrity(damage):
    with zipfile.ZipFile(ROOT/'deliverables/native-prepared-artifact-windows-20260913-r1/transfer/source.zip') as z:
        files={n:z.read(n) for n in z.namelist()}
    target='candidate/runtime/native_asr/asr.cpp'
    if damage=='hash-check':files[target]=files[target].replace(b'prepared->check_unchanged();',b'/* check removed */')
    elif damage=='threads':files[target]=files[target].replace(b'SetIntraOpNumThreads(threads)',b'SetIntraOpNumThreads(1)')
    elif damage=='other-file':files['candidate/runtime/native_asr/frontend.cpp']+=b'\n// unrelated change\n'
    if damage:
        with pytest.raises(AssertionError):validate_source(files.__getitem__,files)
    else:validate_source(files.__getitem__,files)
