"""Package metadata must express known capabilities without inventing resources."""
import copy
import pytest
from scripts.assemble_guided_beta_candidate import release_contract, operator_setup


def example():
    return {'variants': [{'platform': p, 'accelerator': {'memory_bytes': n, 'session_limit': 1,
              'models': ['bound-model'], 'backend': 'metal' if p == 'macos' else 'vulkan'}}
              for p, n in [('macos', 8589934592), ('linux', 8486555648), ('windows', 7482712064)]],
            'settings': [{'key': k, 'default': 'unchanged', 'type': 'enum', 'values': ['unchanged']}
                         for k in ('stt_language', 'turn_pause_ms', 'vad_threshold', 'tts_voice',
                                   'tts_language', 'tts_temperature', 'tts_seed', 'capture_limit_minutes')]}


def test_declarations_preserve_runtime_choices_and_settings():
    cfg = example()
    prior = copy.deepcopy(cfg)
    release_contract(cfg)
    assert cfg.pop('aiios_min_version') == '0.1.7'
    for variant in cfg['variants']:
        assert variant['accelerator'].pop('startup_ms') == 180000
        assert 'device_memory_bytes' not in variant['accelerator']
        assert operator_setup(variant['platform']) == {}
    for setting in cfg['settings']:
        assert setting.pop('scope') == ('hearing' if setting['key'] in
                                        ('stt_language', 'turn_pause_ms', 'vad_threshold', 'capture_limit_minutes') else 'speaking')
    assert cfg == prior


@pytest.mark.parametrize('fault', ['new-setting', 'device-zero', 'missing-memory', 'new-platform'])
def test_unknown_contract_changes_refused_without_partial_mutation(fault):
    cfg = example()
    if fault == 'new-setting': cfg['settings'].append({'key': 'new'})
    if fault == 'device-zero': cfg['variants'][1]['accelerator']['device_memory_bytes'] = 0
    if fault == 'missing-memory': cfg['variants'][1]['accelerator'].pop('memory_bytes')
    if fault == 'new-platform': cfg['variants'][0]['platform'] = 'ios'
    prior = copy.deepcopy(cfg)
    with pytest.raises(ValueError): release_contract(cfg)
    assert cfg == prior
