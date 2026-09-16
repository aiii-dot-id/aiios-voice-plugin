import copy

import pytest

from scripts.audit_native_precomputed_windows import validate_candidate, validate_optimizer_source
from scripts.compare_native_precomputed_windows import candidate_flag


def result():
    return {'candidate_kind': 'editor', 'weights_hardlinked_not_copied': True,
            'candidate_model_root': 'C:/temp/editor-models',
            'bindings': {'C:/temp/editor-models/encoder.onnx': '0929ff8f20c5d3b874ff4561aaf605b2b33199276b4e0af7d8adfe3e8e5b52cf',
                         'C:/temp/editor-models/model.safetensors': '9eebdd6590289cb3030f310858f3df93256600a800a3e8200c5993d5f967e174'},
            'stages': [{'name': 'baseline-configure', 'command': ['cmake', '-S', 'C:/proof/source/runtime/native_asr']},
                       {'name': 'candidate-configure', 'command': ['cmake', '-S', 'C:/proof/source/editor/runtime/native_asr']}]}


def test_source_selected_candidate_has_no_fictitious_flag():
    assert candidate_flag('editor') is None
    validate_candidate(result(), 'editor')


@pytest.mark.parametrize('kind', ['editor', 'editor-no-opt', 'editor-transpose-free'])
def test_optimizer_source_must_match_the_candidate_label(kind):
    class Source:
        def __init__(self, candidate):
            self.candidate = candidate
        def read(self, path):
            level = self.candidate if path.startswith('editor/') else 'ORT_ENABLE_ALL'
            return ('options.SetGraphOptimizationLevel(GraphOptimizationLevel::' + level + ');').encode()
    assert candidate_flag(kind) is None
    wanted = 'ORT_ENABLE_ALL' if kind == 'editor' else 'ORT_DISABLE_ALL'
    validate_optimizer_source(Source(wanted), kind)
    with pytest.raises(AssertionError):
        validate_optimizer_source(Source('ORT_ENABLE_ALL' if wanted == 'ORT_DISABLE_ALL' else 'ORT_DISABLE_ALL'), kind)


@pytest.mark.parametrize('change', ['wrong_arm', 'other_root', 'old_graph', 'other_weights', 'copied_checkpoint'])
def test_mislabeled_or_changed_candidate_is_refused(change):
    r = copy.deepcopy(result())
    if change == 'wrong_arm':
        r['stages'][1]['command'][-1] = r['stages'][0]['command'][-1]
    elif change == 'other_root':
        r['stages'][1]['command'][-1] = 'C:/elsewhere/editor/runtime/native_asr'
    elif change == 'old_graph':
        r['bindings']['C:/temp/editor-models/encoder.onnx'] = '0' * 64
    elif change == 'other_weights':
        r['bindings']['C:/temp/editor-models/model.safetensors'] = '0' * 64
    else:
        r['weights_hardlinked_not_copied'] = False
    with pytest.raises(AssertionError):
        validate_candidate(r, 'editor')
