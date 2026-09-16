import copy
import hashlib
import json
import zipfile

import pytest

from scripts.native_checkpoint_delta import apply_delta, check_resources, compare_settings
from scripts import stage_asr_dml_full_loop as stage


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(tmp_path):
    runtime = tmp_path/'fresh'; (runtime/'bin').mkdir(parents=True)
    (runtime/'bin/DirectML.dll').write_bytes(b'old')
    source = tmp_path/'new.dll'; source.write_bytes(b'new')
    profile = {'files':{'bin/DirectML.dll':{'sha256':digest(b'old'),'bytes':3}}}
    rows = [dict(runtime_path='bin/DirectML.dll', path=str(source), old_sha256=digest(b'old'),
                 sha256=digest(b'new'), must_load=True)]
    def bind(path, expected):
        if path.is_symlink() or digest(path.read_bytes()) != expected:
            raise ValueError('binding differs')
    return runtime, source, profile, rows, bind


def test_only_exact_private_delta_and_case_normalized_image(tmp_path):
    runtime,source,profile,rows,bind = fixture(tmp_path)
    result = apply_delta(runtime,profile,rows,bind)
    assert set(result) == {'directml.dll'}
    assert source.read_bytes() == (runtime/'bin/DirectML.dll').read_bytes() == b'new'
    assert profile['files']['bin/DirectML.dll']['sha256'] == digest(b'new')


@pytest.mark.parametrize('damage', [
    lambda rows: rows[0].update(old_sha256=None),
    lambda rows: rows[0].update(sha256=digest(b'wrong')),
    lambda rows: rows.append(copy.deepcopy(rows[0])),
    lambda rows: rows[0].update(runtime_path='../outside'),
    lambda rows: rows[0].update(runtime_path='resources/settings.json'),
])
def test_delta_refuses_unbound_overwrite_and_scope_before_mutation(tmp_path,damage):
    runtime,source,profile,rows,bind = fixture(tmp_path); damage(rows)
    with pytest.raises(ValueError): apply_delta(runtime,profile,rows,bind)
    assert (runtime/'bin/DirectML.dll').read_bytes() == b'old'


def test_observation_error_or_memory_pressure_is_not_zero():
    rows = [dict(elapsed=1.,system_memory={'available_physical_bytes':16},gpu={'memory_used':3})]
    limits = dict(minimum_available_physical_bytes=8,maximum_gpu_used_bytes=7)
    assert check_resources(rows,[],limits)['samples'] == 1
    for values,errors in (([],[]),(rows,['NVML failed'])):
        with pytest.raises(ValueError): check_resources(values,errors,limits)
    rows[0]['gpu']['memory_used'] = 8
    with pytest.raises(ValueError): check_resources(rows,[],limits)
    rows[0]['gpu']['memory_used'] = 3; rows[0]['system_memory']['available_physical_bytes'] = 7
    with pytest.raises(ValueError): check_resources(rows,[],limits)


def test_tts_change_never_hidden_by_speed():
    proof = dict(passed=True,cases=[dict(name=str(i),pcm_sha256=str(i),samples=24000,seconds=1.) for i in range(13)])
    current = copy.deepcopy(proof)
    assert compare_settings(current,proof)['pcm_identical']
    assert not compare_settings(current,proof)['fair_performance_comparison']
    current['cases'][0].update(seconds=.1,pcm_sha256='wrong')
    with pytest.raises(ValueError): compare_settings(current,proof)


def test_stage_exact_winning_components_without_models(tmp_path):
    stage.stage(tmp_path/'stage')
    with zipfile.ZipFile(tmp_path/'stage/source.zip') as z:
        c=json.loads(z.read('contract.json'))
        assert c['parent']==stage.PARENT and c['parent_freeze_sha256']==stage.PARENT_FREEZE
        rows={r['name']:r for r in c['component_delta']}
        assert rows['aii_native_asr.dll']['sha256']=='4b933702bed72e51670faa68e3c3d85275e0a09dbb5f7a40248ef7e8118fe827'
        assert rows['onnxruntime.dll']['old_sha256']=='0a49bb13573807ad309aa9967a46471c07d6495bab5b279d1fad474aefd8ef4b'
        assert rows['DirectML.dll']['must_load'] and not rows['onnxruntime_providers_shared.dll']['must_load']
        assert c['service_diagnostic_only'] and not c['added_paths']
        assert not any(n.startswith('models/') or n.endswith('.dll') for n in z.namelist())
        assert z.read('support/native_checkpoint_delta.py')==(stage.ROOT/'scripts/native_checkpoint_delta.py').read_bytes()
