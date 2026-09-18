"""Package metadata must express known capabilities without inventing resources."""
import copy
import hashlib
import json
import pytest
from scripts.assemble_guided_beta_candidate import release_contract, operator_setup, candidate_inputs


def example():
    return {'variants': [{'platform': p, 'accelerator': {'memory_bytes': n, 'session_limit': 1, 'startup_ms': 180000,
              'models': ['bound-model'], 'backend': 'metal' if p == 'macos' else 'vulkan'}}
              for p, n in [('macos', 8589934592), ('linux', 8486555648), ('windows', 7482712064)]],
            'settings': [{'key': k, 'scope': 'speaking' if k.startswith('tts_') else 'hearing',
                          'default': 'unchanged', 'type': 'enum', 'values': ['unchanged']}
                         for k in ('stt_language', 'turn_pause_ms', 'vad_threshold', 'tts_voice',
                                   'tts_language', 'tts_temperature', 'tts_seed', 'capture_limit_minutes')]}


def test_declarations_preserve_runtime_choices_and_settings():
    cfg = example()
    prior = copy.deepcopy(cfg)
    release_contract(cfg)
    assert cfg.pop('aiios_min_version') == '0.1.8'
    for variant in cfg['variants']:
        assert variant['accelerator']['startup_ms'] == 180000
        assert 'device_memory_bytes' not in variant['accelerator']
        assert operator_setup(variant['platform']) == {}
    for setting in cfg['settings']:
        assert setting['scope'] == ('hearing' if setting['key'] in
                                        ('stt_language', 'turn_pause_ms', 'vad_threshold', 'capture_limit_minutes') else 'speaking')
    assert cfg == prior


@pytest.mark.parametrize('fault', ['new-setting', 'device-zero', 'missing-memory', 'new-platform', 'missing-scope', 'missing-startup', 'invalid-startup'])
def test_unknown_contract_changes_refused_without_partial_mutation(fault):
    cfg = example()
    if fault == 'new-setting': cfg['settings'].append({'key': 'new'})
    if fault == 'device-zero': cfg['variants'][1]['accelerator']['device_memory_bytes'] = 0
    if fault == 'missing-memory': cfg['variants'][1]['accelerator'].pop('memory_bytes')
    if fault == 'new-platform': cfg['variants'][0]['platform'] = 'ios'
    if fault == 'missing-scope': cfg['settings'][0].pop('scope')
    if fault == 'missing-startup': cfg['variants'][0]['accelerator'].pop('startup_ms')
    if fault == 'invalid-startup': cfg['variants'][0]['accelerator']['startup_ms'] = True
    prior = copy.deepcopy(cfg)
    with pytest.raises(ValueError): release_contract(cfg)
    assert cfg == prior


def test_assembly_preserves_explicit_platform_allowances():
    cfg = example()
    for variant, startup in zip(cfg['variants'], (60000, 120000, 180000)):
        variant['accelerator']['startup_ms'] = startup
    prior = copy.deepcopy(cfg)
    release_contract(cfg)
    cfg.pop('aiios_min_version')
    assert cfg == prior


@pytest.mark.parametrize('startup', [1, 3600000])
def test_startup_declaration_accepts_sdk_boundary(startup):
    cfg = example()
    cfg['variants'][0]['accelerator']['startup_ms'] = startup
    release_contract(cfg)
    assert cfg['variants'][0]['accelerator']['startup_ms'] == startup


def test_release_requires_bound_inputs_and_resource_declarations(tmp_path):
    with pytest.raises(ValueError, match='explicit candidate'):
        candidate_inputs(None)
    rows = {}
    for variant in example()['variants']:
        name = variant['platform']
        stage = tmp_path / name
        stage.mkdir()
        result = stage / 'result.json'
        result.write_text('{}')
        carrier = stage / 'carrier'
        carrier.write_bytes(b'explicit fixture')
        rows[name] = dict(stage=str(stage), carrier=str(carrier),
                          stage_sha256=hashlib.sha256(result.read_bytes()).hexdigest(),
                          carrier_sha256=hashlib.sha256(carrier.read_bytes()).hexdigest(),
                          accelerator=variant['accelerator'])
    manifest = tmp_path / 'inputs.json'
    manifest.write_text(json.dumps(rows))
    stages, carriers, accelerators = candidate_inputs(manifest)
    assert set(stages) == set(carriers) == set(accelerators) == set(rows)
    rows['macos'].pop('accelerator')
    manifest.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match='accelerator'):
        candidate_inputs(manifest)
