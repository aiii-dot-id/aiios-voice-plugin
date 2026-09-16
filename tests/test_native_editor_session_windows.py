import copy

import pytest

from scripts.prove_native_editor_session_windows import validate_arms, component_spec


def test_full_session_requires_the_exact_admitted_component():
    audit = {'candidate_kind': 'editor-transpose-free', 'passed': True,
             'measured': {'performance_gate_passed': True}}
    root, graph = component_spec('editor-transpose-free', audit)
    assert root == 'native-uid-desktops-transpose-free-20260913-r1'
    assert graph == '6b6462ae799bbf31fea30cc72a09565c1b9048559c4f7249053553ccacf2a2be'
    with pytest.raises(AssertionError):
        component_spec('editor', audit)
    with pytest.raises(AssertionError):
        component_spec('editor-no-opt', audit)
    audit['measured']['performance_gate_passed'] = False
    with pytest.raises(AssertionError):
        component_spec('editor-transpose-free', audit)


def profiles():
    common = {'carrier': 'bound.exe', 'uid_model': 'uid', 'policy': 'policy',
              'backend': 'vulkan', 'sdk_revision': 'same-sdk',
              'model_paths': ['original', 'same', 'same', 'same', 'same', 'same', 'same'],
              'libraries': {'aii_voice_runtime.dll': 'worker', 'aii_native_asr.dll': 'original',
                            'native_pocket_resident.dll': 'tts', 'aii_native_uid.dll': 'uid'}}
    candidate = copy.deepcopy(common)
    candidate['libraries']['aii_native_asr.dll'] = 'editor'
    candidate['model_paths'][0] = 'editor'
    return {'baseline': common, 'candidate': candidate}


def test_only_loader_and_graph_may_differ():
    validate_arms(profiles())


@pytest.mark.parametrize('defect', ['tts', 'uid', 'sdk', 'gpu', 'model', 'same_loader', 'same_graph'])
def test_unrelated_change_or_fake_candidate_is_refused(defect):
    value = profiles()
    candidate = value['candidate']
    if defect == 'tts': candidate['libraries']['native_pocket_resident.dll'] = 'other'
    elif defect == 'uid': candidate['libraries']['aii_native_uid.dll'] = 'other'
    elif defect == 'sdk': candidate['sdk_revision'] = 'other'
    elif defect == 'gpu': candidate['backend'] = 'cpu'
    elif defect == 'model': candidate['model_paths'][2] = 'other'
    elif defect == 'same_loader': candidate['libraries']['aii_native_asr.dll'] = 'original'
    else: candidate['model_paths'][0] = 'original'
    with pytest.raises(AssertionError): validate_arms(value)
