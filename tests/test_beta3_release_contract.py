"""Package metadata must express known capabilities without inventing resources."""
import copy
import hashlib
import json
from pathlib import Path
import re
import pytest
from scripts import assemble_guided_beta_candidate as assembly
from scripts.assemble_guided_beta_candidate import (
    release_contract, operator_setup, candidate_inputs, staged_archive, selected_models,
    bind_variants, verify_packaged_compositions, shared_descriptors, measured_reservations,
)


def test_download_selection_uses_qualified_inventory_not_historical_count():
    models=[dict(name='hearing',path='stt/new.onnx',sha256='a'*64,size=41),
            dict(name='voice',path='tts/model',sha256='b'*64,size=42),
            dict(name='foreign',path='endpoint/other',sha256='c'*64,size=43)]
    selection=dict(models=['hearing','voice'])
    stage=dict(models={m['path']:dict(sha256=m['sha256'],bytes=m['size']) for m in models[:2]})
    assert selected_models(models,selection,stage)==models[:2]
    for field,value in [('sha256','d'*64),('size',40),('path','stt/old.onnx')]:
        changed=copy.deepcopy(models);changed[0][field]=value
        with pytest.raises(ValueError,match='qualified model inventory'):
            selected_models(changed,selection,stage)
    for bad in (['voice'],['hearing','voice','foreign'],['hearing','hearing'],['absent']):
        with pytest.raises(ValueError):selected_models(models,dict(models=bad),stage)
    with pytest.raises(ValueError):selected_models(models,selection,{})
    with pytest.raises(ValueError):selected_models(models+[models[0]],selection,stage)
    duplicate=copy.deepcopy(models);duplicate[1]['path']=duplicate[0]['path']
    with pytest.raises(ValueError):selected_models(duplicate,selection,stage)


GPU = {'macos': ('metal', 0), 'linux': ('vulkan', 3 << 30), 'windows': ('vulkan', 2 << 30)}


def example():
    """Honest desktop sets: Metal is unified (device 0), Vulkan reserves device memory."""
    return {'variants': [{'platform': p, 'arch': 'arm64' if p == 'macos' else 'x86_64',
              'variant_id': p+'-native', 'accelerator': {'memory_bytes': n, 'device_memory_bytes': GPU[p][1],
              'required_accelerators': [GPU[p][0]],
              'os': p, 'arch': 'arm64' if p == 'macos' else 'x86_64', 'session_limit': 1, 'startup_ms': 180000,
              'models': ['bound-model'], 'backend': GPU[p][0]}}
              for p, n in [('macos', 8589934592), ('linux', 8486555648), ('windows', 7482712064)]],
            'variant_preference': [p+'-native' for p in ('macos','linux','windows')],
            'settings': [{'key': k, 'scope': 'speaking' if k.startswith('tts_') else 'hearing',
                          'default': 'unchanged', 'type': 'enum', 'values': ['unchanged']}
                         for k in ('stt_language', 'turn_pause_ms', 'vad_threshold', 'tts_voice',
                                   'tts_language', 'tts_temperature', 'tts_seed', 'capture_limit_minutes')]}


def measured(cfg, runtime='e' * 64):
    """Stand-in for each GPU set's stage-bound complete-composition measurement."""
    rows = {}
    for v in cfg['variants']:
        a = v['accelerator']
        domains = [d for d in a['backend'].split('+') if d != 'cpu']
        if domains:
            budget = a['device_memory_bytes'] or a['memory_bytes']
            rows[v['variant_id']] = dict(schema='aiii.voice.accelerator-reservation-evidence.v1',
                passed=True, complete_composition=True, variant_id=v['variant_id'],
                runtime_manifest_sha256=runtime,
                domains={d: dict(measured_peak_bytes=budget // 2) for d in domains})
    return rows


def test_declarations_preserve_runtime_choices_and_settings():
    cfg = example()
    prior = copy.deepcopy(cfg)
    release_contract(cfg, '1.2.3', measured(cfg))
    assert cfg.pop('aiios_min_version') == '1.2.3'
    for variant in cfg['variants']:
        assert variant['accelerator']['startup_ms'] == 180000
        assert variant['accelerator']['device_memory_bytes'] == GPU[variant['platform']][1]
        assert operator_setup(variant['platform']) == {}
    for setting in cfg['settings']:
        assert setting['scope'] == ('hearing' if setting['key'] in
                                        ('stt_language', 'turn_pause_ms', 'vad_threshold', 'capture_limit_minutes') else 'speaking')
    assert cfg == prior


@pytest.mark.parametrize('fault', ['new-setting', 'device-negative', 'missing-device', 'device-bool',
    'device-unrequired', 'missing-memory', 'new-platform', 'missing-scope', 'missing-startup',
    'invalid-startup', 'coordinates', 'duplicate-variant', 'missing-preference', 'partial-preference'])
def test_unknown_contract_changes_refused_without_partial_mutation(fault):
    cfg = example()
    evidence = measured(cfg)
    if fault == 'new-setting': cfg['settings'].append({'key': 'new'})
    if fault == 'device-negative': cfg['variants'][1]['accelerator']['device_memory_bytes'] = -1
    if fault == 'missing-device': cfg['variants'][1]['accelerator'].pop('device_memory_bytes')
    if fault == 'device-bool': cfg['variants'][1]['accelerator']['device_memory_bytes'] = True
    if fault == 'device-unrequired': cfg['variants'][1]['accelerator'].pop('required_accelerators')
    if fault == 'missing-memory': cfg['variants'][1]['accelerator'].pop('memory_bytes')
    if fault == 'new-platform': cfg['variants'][0]['platform'] = 'ios'
    if fault == 'missing-scope': cfg['settings'][0].pop('scope')
    if fault == 'missing-startup': cfg['variants'][0]['accelerator'].pop('startup_ms')
    if fault == 'invalid-startup': cfg['variants'][0]['accelerator']['startup_ms'] = True
    if fault == 'coordinates': cfg['variants'][0]['accelerator']['os'] = 'linux'
    if fault == 'duplicate-variant': cfg['variants'].append(copy.deepcopy(cfg['variants'][0]))
    if fault == 'missing-preference': cfg.pop('variant_preference')
    if fault == 'partial-preference': cfg['variant_preference'].pop()
    prior = copy.deepcopy(cfg)
    with pytest.raises(ValueError): release_contract(cfg, '1.2.3', evidence)
    assert cfg == prior


def test_assembly_preserves_explicit_platform_allowances():
    cfg = example()
    for variant, startup in zip(cfg['variants'], (60000, 120000, 180000)):
        variant['accelerator']['startup_ms'] = startup
    prior = copy.deepcopy(cfg)
    release_contract(cfg, '1.2.3', measured(cfg))
    cfg.pop('aiios_min_version')
    assert cfg == prior


@pytest.mark.parametrize('startup', [1, 3600000])
def test_startup_declaration_accepts_sdk_boundary(startup):
    cfg = example()
    cfg['variants'][0]['accelerator']['startup_ms'] = startup
    release_contract(cfg, '1.2.3', measured(cfg))
    assert cfg['variants'][0]['accelerator']['startup_ms'] == startup


@pytest.mark.parametrize('version', ['', None, True, '1.2.3\n', ' 1.2.3'])
def test_minimum_host_cannot_be_guessed(version):
    cfg = example()
    prior = copy.deepcopy(cfg)
    with pytest.raises(ValueError, match='explicit minimum host'):
        release_contract(cfg, version, measured(cfg))
    assert cfg == prior


def test_explicit_newer_host_requirement_is_not_reset_to_historical_release():
    cfg = example()
    cfg['aiios_min_version'] = '1.2.3'
    release_contract(cfg, '1.2.3', measured(cfg))
    assert cfg['aiios_min_version'] == '1.2.3'


def documented_selection_floor():
    """The floor is whatever the selection contract documents, not a test constant."""
    text = ' '.join((Path(__file__).resolve().parents[1]/'docs/RUNTIME_COMPONENT_SELECTION.md').read_text().split())
    found = re.findall(r'minimum host version is ([0-9]+\.[0-9]+\.[0-9]+)', text)
    assert len(found) == 1, 'the selection contract must state exactly one host floor'
    return found[0]


def test_assembler_floor_is_the_documented_selection_floor():
    assert getattr(assembly, 'COMPONENT_SELECTION_MIN_HOST', None) == documented_selection_floor()


def neighbours(floor):
    major, minor, patch = map(int, floor.split('.'))
    below = [f'{major}.{minor}.{patch-1}'] if patch else []
    below += [f'{major}.{minor-1}.{patch+99}'] if minor else []
    below += [f'{major-1}.{minor+99}.{patch+99}'] if major else []
    above = [floor, f'{major}.{minor}.{patch+1}', f'{major}.{minor+1}.0', f'{major+1}.0.0',
             f'{major}.{minor}.0{patch}']  # the host grammar permits leading zeros
    return below, above


def test_signed_selection_package_cannot_claim_a_host_below_the_documented_floor():
    below, above = neighbours(documented_selection_floor())
    assert below, 'a floor of 0.0.0 cannot be falsified'
    for version in below:
        cfg = example()
        prior = copy.deepcopy(cfg)
        with pytest.raises(ValueError, match='minimum host'):
            release_contract(cfg, version, measured(cfg))
        assert cfg == prior, version
    for version in above:
        cfg = example()
        release_contract(cfg, version, measured(cfg))
        assert cfg['aiios_min_version'] == version  # supplied exactly, never rewritten


@pytest.mark.parametrize('version', ['0.1.12-rc.1', '0.1.12+build', 'v0.1.12', '0.1', '0.1.12.0.0', '1.2.x'])
def test_minimum_host_uses_the_host_window_grammar(version):
    cfg = example()
    prior = copy.deepcopy(cfg)
    with pytest.raises(ValueError, match='minimum host'):
        release_contract(cfg, version, measured(cfg))
    assert cfg == prior


def bound_input_fixture(tmp_path):
    rows = {}
    for variant in example()['variants']:
        name = variant['variant_id']
        stage = tmp_path / name
        stage.mkdir()
        result = stage / 'result.json'
        result.write_text('{}')
        carrier = stage / 'carrier'
        carrier.write_bytes(b'explicit fixture')
        rows[name] = dict(platform=variant['platform'], arch=variant['arch'],
                          stage=str(stage), carrier=str(carrier),
                          stage_sha256=hashlib.sha256(result.read_bytes()).hexdigest(),
                          carrier_sha256=hashlib.sha256(carrier.read_bytes()).hexdigest(),
                          accelerator=variant['accelerator'])
    manifest = tmp_path / 'inputs.json'
    declaration = dict(variants=rows, variant_preference=list(rows))
    manifest.write_text(json.dumps(declaration))
    return manifest, declaration


def test_release_requires_bound_inputs_and_resource_declarations(tmp_path):
    with pytest.raises(ValueError, match='explicit candidate'):
        candidate_inputs(None)
    manifest, declaration = bound_input_fixture(tmp_path)
    rows = declaration['variants']
    bindings, order = candidate_inputs(manifest)
    assert set(bindings) == set(rows) and order == list(rows)
    assert bindings['macos-native']['stage'] == tmp_path/'macos-native'
    rows['macos-native'].pop('accelerator')
    manifest.write_text(json.dumps(declaration))
    with pytest.raises(ValueError, match='accelerator'):
        candidate_inputs(manifest)


@pytest.mark.parametrize('fault', ['stage', 'carrier'])
def test_candidate_input_changed_bytes_are_refused(tmp_path, fault):
    manifest, declaration = bound_input_fixture(tmp_path)
    row = declaration['variants']['windows-native']
    changed = Path(row['stage'])/'result.json' if fault == 'stage' else Path(row['carrier'])
    changed.write_bytes(b'changed after binding')
    with pytest.raises(ValueError, match='changed'):
        candidate_inputs(manifest)


def evidence_inputs(tmp_path):
    manifest, declaration = bound_input_fixture(tmp_path)
    for name, row in measured(example()).items():
        path = tmp_path / (name + '-reservation.json')
        path.write_text(json.dumps(row))
        declaration['variants'][name]['reservation_evidence'] = dict(
            path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    manifest.write_text(json.dumps(declaration))
    return manifest, declaration


def test_reservation_evidence_is_read_from_its_exact_bytes_and_bound_to_the_stage(tmp_path):
    manifest, _ = evidence_inputs(tmp_path)
    bindings, _ = candidate_inputs(manifest)
    stages = {v: dict(runtime_manifest_sha256='e' * 64) for v in bindings}
    evidence = measured_reservations(bindings, stages)
    assert set(evidence) == {'macos-native', 'linux-native', 'windows-native'}
    cfg = example()
    release_contract(cfg, '1.2.3', evidence)
    stages['linux-native']['runtime_manifest_sha256'] = 'f' * 64  # measured another runtime
    with pytest.raises(ValueError, match='different composition'):
        measured_reservations(bindings, stages)


@pytest.mark.parametrize('fault', ['changed', 'extra-key', 'not-object', 'duplicate-key'])
def test_reservation_evidence_binding_cannot_drift(tmp_path, fault):
    manifest, declaration = evidence_inputs(tmp_path)
    binding = declaration['variants']['windows-native']['reservation_evidence']
    if fault == 'changed': Path(binding['path']).write_text('{}')
    if fault == 'extra-key': binding['measured_peak_bytes'] = 1
    if fault == 'not-object': declaration['variants']['windows-native']['reservation_evidence'] = binding['path']
    if fault == 'duplicate-key':
        path = Path(binding['path'])
        path.write_text(path.read_text().replace('"passed": true', '"passed": false, "passed": true'))
        binding['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(declaration))
    with pytest.raises(ValueError):
        candidate_inputs(manifest)


@pytest.mark.parametrize('fault', ['variant', 'resource', 'preference'])
def test_duplicate_candidate_keys_do_not_silently_replace_bindings(tmp_path, fault):
    manifest, declaration = bound_input_fixture(tmp_path)
    raw = json.dumps(declaration)
    if fault == 'variant':
        raw = raw.replace('"windows-native": {', '"windows-native": {}, "windows-native": {')
    if fault == 'resource':
        raw = raw.replace('"memory_bytes":', '"memory_bytes": 1, "memory_bytes":', 1)
    if fault == 'preference':
        raw = raw.replace('"variant_preference":', '"variant_preference": [], "variant_preference":')
    manifest.write_text(raw)
    with pytest.raises(ValueError, match='duplicate candidate input key'):
        candidate_inputs(manifest)


def two_sets():
    cfg = example()
    rows = {}
    for v in cfg['variants']:
        for target in ('full', 'small'):
            name = v['platform']+'-'+target
            rows[name] = dict(platform=v['platform'], arch=v['arch'],
                             accelerator=copy.deepcopy(v['accelerator']))
            rows[name]['accelerator']['memory_bytes'] //= 2 if target == 'small' else 1
    return cfg, rows, list(rows)


def test_two_component_sets_on_each_desktop_preserve_explicit_choices():
    cfg, rows, order = two_sets()
    cfg['default_variant'] = 'retired-default'
    bind_variants(cfg, rows, order)
    before = copy.deepcopy(cfg)
    release_contract(cfg, '0.1.12', measured(cfg))
    cfg.pop('aiios_min_version')
    assert cfg == before and len(cfg['variants']) == 6
    assert 'default_variant' not in cfg
    for v in cfg['variants']:
        assert v['accelerator'] == rows[v['variant_id']]['accelerator']


def test_device_reservation_and_required_domains_are_not_invented():
    cfg = example()
    profile = cfg['variants'][1]['accelerator']
    profile.update(backend='cuda+vulkan', device_memory_bytes=3<<30, required_accelerators=['cuda', 'vulkan'])
    before = copy.deepcopy(cfg)
    release_contract(cfg, '0.1.12', measured(cfg))
    cfg.pop('aiios_min_version')
    assert cfg == before


def test_cpu_set_declares_zero_device_memory_without_gpu_evidence():
    cfg = example()
    cfg['variants'][2]['accelerator'].update(backend='cpu', device_memory_bytes=0)
    cfg['variants'][2]['accelerator'].pop('required_accelerators')
    evidence = measured(cfg)
    assert 'windows-native' not in evidence
    release_contract(cfg, '1.2.3', evidence)


DOMAINS, EVIDENCE = 'accelerator domains', 'measured complete-composition evidence'


@pytest.mark.parametrize('fault,refusal', [
    ('device-zero', 'positive measured device reservation'), ('device-zero-unrequired', DOMAINS),
    ('vulkan-unrequired', DOMAINS), ('metal-unrequired', DOMAINS),
    ('metal-device', 'unified accelerator footprint'), ('relabelled-cuda', DOMAINS),
    ('extra-domain', DOMAINS), ('unknown-backend', 'no measurable accelerator domain'),
    ('unmeasurable-domain', 'no measurable accelerator domain'), ('cpu-requires', DOMAINS),
    ('cpu-device', DOMAINS), ('no-evidence', EVIDENCE), ('peak-above-device', 'below its measured peak'),
    ('unified-peak-above-host', 'below its measured peak'), ('evidence-other-set', EVIDENCE),
    ('evidence-other-domain', EVIDENCE), ('evidence-not-passed', EVIDENCE),
    ('evidence-partial-composition', EVIDENCE), ('evidence-unbound-runtime', EVIDENCE),
    ('evidence-peak-missing', 'measured peak missing')])
def test_gpu_reservation_needs_measured_justification(fault, refusal):
    """Restores the removed 'device-zero' falsifier and binds reservations to evidence."""
    cfg = example()
    evidence = measured(cfg)
    mac, linux, windows = (v['accelerator'] for v in cfg['variants'])
    if fault == 'device-zero': linux['device_memory_bytes'] = 0
    if fault == 'device-zero-unrequired': linux.update(device_memory_bytes=0); linux.pop('required_accelerators')
    if fault == 'vulkan-unrequired': windows.pop('required_accelerators')
    if fault == 'metal-unrequired': mac.pop('required_accelerators')
    if fault == 'metal-device': mac['device_memory_bytes'] = 1 << 30
    if fault == 'relabelled-cuda': linux['required_accelerators'] = ['cuda']
    if fault == 'extra-domain': linux['required_accelerators'] = ['vulkan', 'cuda']
    if fault == 'unknown-backend': windows.update(backend='directml', required_accelerators=['directml'])
    if fault == 'unmeasurable-domain': mac.update(backend='vulkan', required_accelerators=['vulkan'])
    if fault == 'cpu-requires': windows.update(backend='cpu', device_memory_bytes=0)
    if fault == 'cpu-device': windows.update(backend='cpu'); windows.pop('required_accelerators')
    if fault == 'no-evidence': evidence.pop('linux-native')
    if fault == 'peak-above-device': evidence['linux-native']['domains']['vulkan']['measured_peak_bytes'] = (3 << 30) + 1
    if fault == 'unified-peak-above-host': evidence['macos-native']['domains']['metal']['measured_peak_bytes'] = mac['memory_bytes'] + 1
    if fault == 'evidence-other-set': evidence['linux-native']['variant_id'] = 'windows-native'
    if fault == 'evidence-other-domain': evidence['linux-native']['domains'] = {'cuda': dict(measured_peak_bytes=1)}
    if fault == 'evidence-not-passed': evidence['windows-native']['passed'] = False
    if fault == 'evidence-partial-composition': evidence['windows-native'].pop('complete_composition')
    if fault == 'evidence-unbound-runtime': evidence['windows-native']['runtime_manifest_sha256'] = 'unbound'
    if fault == 'evidence-peak-missing': evidence['windows-native']['domains']['vulkan'] = {}
    prior = copy.deepcopy(cfg)
    with pytest.raises(ValueError, match=refusal):
        release_contract(cfg, '1.2.3', evidence)
    assert cfg == prior


def test_architecture_mismatch_does_not_partially_expand_config():
    cfg, rows, order = two_sets()
    rows[order[-1]]['arch'] = 'arm64'
    before = copy.deepcopy(cfg)
    with pytest.raises(ValueError, match='architecture'):
        bind_variants(cfg, rows, order)
    assert cfg == before


@pytest.mark.parametrize('order', [[], ['macos-native']*3, ['macos-native','linux-native','absent'], 'macos-native'])
def test_candidate_input_preference_cannot_guess_or_repeat_sets(tmp_path, order):
    path = tmp_path/'input.json'
    path.write_text(json.dumps(dict(variants=dict.fromkeys(example()['variant_preference'], {}),
                                    variant_preference=order)))
    with pytest.raises(ValueError, match='exactly once'):
        candidate_inputs(path)


def test_staged_callable_contracts_must_agree_without_first_platform_guess():
    ops = [dict(id='speaker.list', summary='List speaker buckets')]
    stages = {v: dict(descriptors=copy.deepcopy(ops)) for v in two_sets()[2]}
    assert shared_descriptors(stages) == ops
    stages['windows-small']['descriptors'][0]['summary'] = 'Different contract'
    with pytest.raises(ValueError, match='disagree'):
        shared_descriptors(stages)
    stages['windows-small'].pop('descriptors')
    with pytest.raises(ValueError, match='re-stage'):
        shared_descriptors(stages)


def packed_fixture(tmp_path):
    cfg, rows, order = two_sets()
    bind_variants(cfg, rows, order)
    cfg['models'] = [dict(name='common', path='common.onnx')]
    cfg['runtimes'] = [dict(variant_id=v, sha256=v) for v in order]
    manifest = dict(variants=[], variant_preference=list(order))
    files = {name: json.dumps(cfg[key]).encode() for name, key in
             [('models.json','models'), ('settings.json','settings')]}
    files['runtime.json'] = json.dumps(dict(runtimes=cfg['runtimes'])).encode()
    files['accelerator.json'] = json.dumps({v['variant_id']:v['accelerator'] for v in cfg['variants']}).encode()
    carriers = {}
    for v in cfg['variants']:
        name = v['variant_id']
        path = tmp_path/name
        path.write_bytes(name.encode())
        carriers[name] = path
        manifest['variants'].append(dict(variant_id=name, platform=v['platform'], arch=v['arch'],
                                         entrypoint='payloads/'+name))
        files['payloads/'+name] = path.read_bytes()
    return manifest, files, cfg, carriers


def test_packaged_bytes_keep_six_compositions_distinct(tmp_path):
    verify_packaged_compositions(*packed_fixture(tmp_path))


@pytest.mark.parametrize('fault', ['collapse', 'preference', 'carrier', 'models', 'runtime', 'coordinates', 'memory', 'device', 'selection'])
def test_sdk_roundtrip_cannot_substitute_a_set(tmp_path, fault):
    manifest, files, cfg, carriers = packed_fixture(tmp_path)
    if fault == 'collapse': manifest['variants'].pop()
    if fault == 'preference': manifest['variant_preference'].reverse()
    if fault == 'carrier': files['payloads/windows-small'] = b'foreign'
    if fault == 'models': files['models.json'] = b'[]'
    if fault == 'runtime': files['runtime.json'] = b'{"runtimes":[]}'
    if fault == 'coordinates': manifest['variants'][-1]['arch'] = 'arm64'
    if fault in ('memory','device','selection'):
        profiles = json.loads(files['accelerator.json'])
        key = dict(memory='memory_bytes', device='device_memory_bytes', selection='models')[fault]
        profiles['windows-small'][key] = [] if fault == 'selection' else -1
        files['accelerator.json'] = json.dumps(profiles).encode()
    with pytest.raises(ValueError):
        verify_packaged_compositions(manifest, files, cfg, carriers)


def test_portable_stage_receipt_names_its_colocated_archive(tmp_path):
    assert staged_archive(tmp_path,'windows-runtime.tar.gz')==tmp_path/'windows-runtime.tar.gz'
    assert staged_archive(tmp_path,str(tmp_path/'old-absolute.tar.gz'))==tmp_path/'old-absolute.tar.gz'


@pytest.mark.parametrize('name',['','.', '..','../escape.tar.gz','nested/archive.tar.gz','bad:archive'])
def test_relative_stage_path_cannot_escape_or_guess_foreign_os_path(tmp_path,name):
    with pytest.raises(ValueError):staged_archive(tmp_path,name)


# The SDK's own extent vectors (aii-plugin-sdk f8b4961 vectors/runtime_extent.json),
# copied byte for byte so assembly is held to the rule the host and SDK share.
EXTENT_VECTORS = Path(__file__).resolve().parent / 'fixtures/sdk_runtime_extent.json'
EXTENT_VECTORS_SHA256 = '2091b19dee98a640e8bad6f883d10fc21e5b78b90112303a2dd2db90463bce4d'
# The macOS Small beta.7 stage receipt's archive: 604 files, 215,715,587 bytes.
STAGED = dict(path='macos-arm64-small-runtime.tar.gz', sha256='e' * 64, size=169832384, files=604,
              installed_bytes=215715587, inventory_sha256='4' * 64, largest_file_bytes=37761698, depth=6)


def extent_vectors():
    raw = EXTENT_VECTORS.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == EXTENT_VECTORS_SHA256
    return json.loads(raw)


def staged_declaration(variant='macos-arm64-small'):
    return assembly.runtime_declaration(dict(STAGED), variant, '0.1.0-beta.7')[1]


def test_extent_floor_is_the_sdk_floor():
    assert assembly.RUNTIME_EXTENT_MIN_HOST == extent_vectors()['floor'] == '0.1.14'


def test_runtime_extent_follows_the_sdk_authoring_vectors():
    vectors = extent_vectors()
    base = json.dumps(vectors['base'])[:-1]
    checked = 0
    for case in vectors['declaration']:
        decl = json.loads(base + case['extent'] + '}')
        if case['min'] == '':
            # Assembly always states its floor; the SDK's floor-less package does not apply.
            with pytest.raises(ValueError, match='explicit minimum host'):
                assembly.runtime_extent([decl], case['min'])
            continue
        try:
            assembly.runtime_extent([decl], case['min'])
            accepted = True
        except ValueError:
            accepted = False
        assert accepted == case['author'], case['name']
        checked += 1
    assert checked == 19


def test_runtime_declaration_carries_the_stage_extent():
    name, decl = assembly.runtime_declaration(dict(STAGED, archive='/absolute/stage/archive'),
                                              'macos-arm64-small', '0.1.0-beta.7')
    assert name == 'e' * 64 + '-macos-arm64-small-runtime.tar.gz'
    assert decl == dict(sha256='e' * 64, size=169832384, files=604, installed_bytes=215715587,
                        inventory_sha256='4' * 64, largest_file_bytes=37761698, depth=6,
                        variant_id='macos-arm64-small', url=assembly.RELEASE_DOWNLOAD + '0.1.0-beta.7/' + name)
    assembly.runtime_extent([decl], '0.1.14')
    for field in ('largest_file_bytes', 'depth'):
        receipt = dict(STAGED); del receipt[field]
        with pytest.raises(KeyError):
            assembly.runtime_declaration(receipt, 'macos-arm64-small', '0.1.0-beta.7')


@pytest.mark.parametrize('version', ['0.1.14', '0.1.15', '0.2.0', '1.0.0', '0.1.014'])
def test_extentless_runtime_is_refused_at_the_extent_floor(version):
    bare = staged_declaration('linux-x86_64-small')
    del bare['largest_file_bytes'], bare['depth']
    with pytest.raises(ValueError, match='declare largest_file_bytes and depth'):
        assembly.runtime_extent([staged_declaration(), bare], version)


@pytest.mark.parametrize('version', ['0.1.13', '0.1.12'])
def test_runtime_extent_is_refused_below_the_extent_floor(version):
    with pytest.raises(ValueError, match='require aiios_min_version >= 0.1.14'):
        assembly.runtime_extent([staged_declaration()], version)
    for field in ('largest_file_bytes', 'depth'):
        half = staged_declaration(); del half[field]
        with pytest.raises(ValueError, match='together or not at all'):
            assembly.runtime_extent([half], version)
    # The component-selection floor alone still admits these hosts.
    cfg = example()
    release_contract(cfg, version, measured(cfg))


@pytest.mark.parametrize('fault', ['largest', 'depth-with-root', 'shallower', 'old-receipt'])
def test_staged_extent_must_be_measured_from_its_archive_tree(fault):
    rows = {'bin/aii_voice_worker': dict(bytes=10), 'lib/nested/libx.dylib': dict(bytes=300),
            'voice-runtime.json': dict(bytes=5)}
    declaration = dict(largest_file_bytes=300, depth=3)
    assert assembly.archive_extent(declaration, rows) == (300, 3)
    if fault == 'largest': declaration['largest_file_bytes'] = 299
    if fault == 'depth-with-root': declaration['depth'] = 4  # as the stage's archive budget counts it
    if fault == 'shallower': declaration['depth'] = 2
    if fault == 'old-receipt': declaration = {}
    with pytest.raises(ValueError, match='re-stage'):
        assembly.archive_extent(declaration, rows)


@pytest.mark.parametrize('fault', [None, 'dropped', 'other-number'])
def test_packaged_runtime_json_keeps_the_staged_extent(tmp_path, fault):
    manifest, files, cfg, carriers = packed_fixture(tmp_path)
    cfg['runtimes'] = [staged_declaration(v) for v in cfg['variant_preference']]
    packed = copy.deepcopy(cfg['runtimes'])
    if fault == 'dropped':
        for decl in packed: del decl['largest_file_bytes'], decl['depth']
    if fault == 'other-number': packed[0]['largest_file_bytes'] += 1
    files['runtime.json'] = json.dumps(dict(runtimes=packed)).encode()
    if fault is None:
        verify_packaged_compositions(manifest, files, cfg, carriers)
        return
    with pytest.raises(ValueError, match='packaged runtime bindings differ'):
        verify_packaged_compositions(manifest, files, cfg, carriers)


def test_authoring_sdk_must_read_the_extent_at_its_floor(tmp_path):
    source = tmp_path / 'sdk/pkg/aiiospkg/runtime.go'
    source.parent.mkdir(parents=True)
    source.write_text('package aiiospkg\n\nconst RuntimeExtentMinHost = "0.1.14"\n')
    assembly.authoring_extent_floor(tmp_path / 'sdk')
    for text in ('package aiiospkg\n', 'const RuntimeExtentMinHost = "0.1.15"\n',
                 '// const RuntimeExtentMinHost = "0.1.14"\n'):
        source.write_text(text)
        with pytest.raises(ValueError, match='does not read runtime extents'):
            assembly.authoring_extent_floor(tmp_path / 'sdk')
