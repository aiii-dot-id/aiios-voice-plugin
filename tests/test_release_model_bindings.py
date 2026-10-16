"""Publication must consume actual model/notice bindings, not an old verdict."""
import copy
import json

import pytest

from scripts.stage_desktop_publication import conditional_downloads, dependencies, platform_plan


def fixture():
    model = dict(name='speaker', path='uid/model.onnx', sha256='a' * 64, size=41,
                 url='https://github.com/example/release/model.onnx')
    variants = [dict(variant_id=p, platform=p, artifact_hash='sha256:' + 'b' * 64)
                for p in ('macos', 'linux', 'windows')]
    runtimes = [dict(variant_id=v['variant_id'], sha256='c' * 64, size=71,
                    url='https://github.com/example/release/runtime.tar.gz') for v in variants]
    profiles = {v['variant_id']: dict(models=['speaker']) for v in variants}
    index = dict(distribution_review_complete=True, open_items=[], models=[
        dict(path=model['path'], sha256=model['sha256'], bytes=model['size'], notice_group='speaker')])
    return dict(variants=variants), dict(models=model, runtimes=runtimes, profiles=profiles, index=index)


def payloads(data):
    files = {name: json.dumps(value).encode() for name, value in (
        ('models.json', [data['models']]), ('runtime.json', dict(runtimes=data['runtimes'])),
        ('accelerator.json', data['profiles']), ('notices/INDEX.json', data['index']))}
    files['notices/speaker/NOTICE'] = b'Upstream attribution'
    return files


def test_current_model_binding_is_accepted_without_upgrading_legal_verdict():
    manifest, data = fixture()
    data['index']['distribution_review_complete'] = False
    data['index']['open_items'] = ['Still needs review']
    before = copy.deepcopy(data)
    assert len(dependencies(manifest, payloads(data))[2]) == 3
    assert data == before


@pytest.mark.parametrize('damage', ['missing-model', 'old-digest', 'old-size',
    'boolean-size', 'empty-group', 'duplicate-path', 'no-models', 'no-index', 'malformed-index'])
def test_old_complete_verdict_cannot_hide_broken_bindings(damage):
    manifest, data = fixture()
    row = data['index']['models'][0]
    if damage == 'missing-model': data['index']['models'].clear()
    if damage == 'old-digest': row['sha256'] = 'd' * 64
    if damage == 'old-size': row['bytes'] += 1
    if damage == 'boolean-size': row['bytes'] = True
    if damage == 'empty-group': row['notice_group'] = ' '
    if damage == 'duplicate-path': data['index']['models'].append(copy.deepcopy(row))
    if damage == 'no-models': del data['index']['models']
    files = payloads(data)
    if damage == 'no-index': del files['notices/INDEX.json']
    if damage == 'malformed-index': files['notices/INDEX.json'] = b'{'
    with pytest.raises(ValueError): dependencies(manifest, files)


@pytest.mark.parametrize('host', ['checkpoint.invalid', 'localhost', '127.0.0.1', '192.0.2.1'])
@pytest.mark.parametrize('target', ['model', 'runtime'])
def test_unpublishable_dependency_host_is_refused(host, target):
    manifest, data = fixture()
    row = data['models'] if target == 'model' else data['runtimes'][0]
    row['url'] = 'https://' + host + '/model.bin'
    with pytest.raises(ValueError, match='download host'): dependencies(manifest, payloads(data))


def test_original_notices_may_be_retained_without_rebinding_retired_models():
    manifest, data = fixture()
    data['index']['models'].append(dict(path='retired/model.onnx', sha256='d' * 64,
                                     bytes=8, notice_group='retired'))
    assert len(dependencies(manifest, payloads(data))[0]) == 1


def multi_set():
    manifest, data = fixture()
    manifest['variants'] = [dict(v, variant_id=v['platform']+'-'+size)
                            for v in manifest['variants'] for size in ('full','small')]
    manifest['variant_preference'] = [v['variant_id'] for v in manifest['variants']]
    data['runtimes'] = [dict(variant_id=v['variant_id'], sha256='c'*64, size=71, installed_bytes=100,
                            url='https://github.com/example/release/runtime.tar.gz')
                       for v in manifest['variants']]
    data['profiles'] = {v['variant_id']:dict(models=['speaker'], memory_bytes=i+1)
                        for i,v in enumerate(manifest['variants'])}
    return manifest, data


def test_publication_and_setup_preserve_all_six_sets():
    manifest, data = multi_set()
    _, _, targets = dependencies(manifest, payloads(data))
    assert set(targets) == set(manifest['variant_preference'])
    setup = dict(platforms={p:{} for p in ('macos','linux','windows')},automatic_configuration=False)
    plans = platform_plan(targets, setup)
    assert set(plans) == set(targets)
    assert len({p['accelerator']['memory_bytes'] for p in plans.values()}) == 6
    assert all(p['operator_config_merge'] == {} for p in plans.values())


@pytest.mark.parametrize('fault', ['no-preference','duplicate','unknown','runtime-census','profile-census'])
def test_incomplete_multi_set_publication_refused(fault):
    manifest, data = multi_set()
    if fault == 'no-preference': manifest.pop('variant_preference')
    if fault == 'duplicate': manifest['variant_preference'][-1] = manifest['variant_preference'][0]
    if fault == 'unknown': manifest['variant_preference'][-1] = 'absent'
    if fault == 'runtime-census': data['runtimes'].pop()
    if fault == 'profile-census': data['profiles'].pop('windows-small')
    with pytest.raises(ValueError):
        dependencies(manifest, payloads(data))


@pytest.mark.parametrize('damage', ['missing', 'empty', 'invented-group', 'parent-path'])
def test_named_group_must_have_a_packaged_notice(damage):
    manifest, data = fixture()
    if damage == 'invented-group': data['index']['models'][0]['notice_group'] = 'invented'
    if damage == 'parent-path': data['index']['models'][0]['notice_group'] = '../speaker'
    files = payloads(data)
    if damage == 'missing': del files['notices/speaker/NOTICE']
    if damage == 'empty': files['notices/speaker/NOTICE'] = b''
    with pytest.raises(ValueError, match='included notice'):
        dependencies(manifest, files)


def test_current_uid_replacement_notice_is_bound_without_promoting_release():
    manifest, data = fixture()
    data['index']['distribution_review_complete'] = False
    data['index']['uid_replacement'] = dict(model_sha256=data['models']['sha256'],
        record='notices/speaker/NOTICE')
    assert len(dependencies(manifest, payloads(data))[0]) == 1
    assert data['index']['distribution_review_complete'] is False


@pytest.mark.parametrize('damage', ['old-model', 'absent-record', 'empty-record',
    'wrong-group', 'parent-path', 'absolute-path', 'not-object', 'not-string'])
def test_stale_uid_replacement_cannot_contradict_current_model(damage):
    manifest, data = fixture()
    replacement = dict(model_sha256=data['models']['sha256'], record='notices/speaker/NOTICE')
    data['index']['uid_replacement'] = replacement
    if damage == 'old-model': replacement['model_sha256'] = 'd' * 64
    if damage == 'absent-record': replacement['record'] = 'notices/speaker/absent'
    if damage == 'wrong-group': replacement['record'] = 'notices/old/NOTICE'
    if damage == 'parent-path': replacement['record'] = 'notices/speaker/../speaker/NOTICE'
    if damage == 'absolute-path': replacement['record'] = '/notices/speaker/NOTICE'
    if damage == 'not-object': data['index']['uid_replacement'] = 'old'
    if damage == 'not-string': replacement['record'] = []
    files = payloads(data)
    if damage == 'empty-record': files['notices/speaker/NOTICE'] = b''
    files['notices/old/NOTICE'] = b'Retired attribution'
    with pytest.raises(ValueError): dependencies(manifest, files)


def test_explicitly_superseded_uid_record_may_remain_for_history():
    manifest, data = fixture()
    data['index']['superseded_uid_replacements'] = [dict(model_sha256='d'*64)]
    assert len(dependencies(manifest, payloads(data))[0]) == 1


@pytest.mark.parametrize('path', ['stt/separator.onnx', 'stt-small/separator-coreml/stage0/weights.bin'])
def test_readback_refuses_separator_weights_under_nvidia_hearing_terms(path):
    manifest, data = fixture()
    data['models'] = dict(data['models'], path=path)
    data['index']['models'][0].update(path=path, notice_group='native-multitalker')
    files = payloads(data)
    files['notices/native-multitalker/NOTICE'] = b'NVIDIA Open Model License attribution'
    with pytest.raises(ValueError, match='separator'):
        dependencies(manifest, files)
    data['index']['models'][0]['notice_group'] = 'speaker'
    assert len(dependencies(manifest, payloads(data))[0]) == 1


def with_language(data, values=('fr',)):
    """The fixture with one more model, needed only for some values of an enum setting."""
    pack = dict(name='language-fr', path='tts/languages/french/model.safetensors', sha256='e' * 64, size=9,
                url='https://github.com/example/release/french.safetensors',
                when=dict(setting='tts_language', values=list(values)))
    data['index']['models'].append(dict(path=pack['path'], sha256=pack['sha256'], bytes=pack['size'], notice_group='speaker'))
    files = payloads(data)
    files['models.json'] = json.dumps([data['models'], pack]).encode()
    files['settings.json'] = json.dumps([dict(key='tts_language', type='enum', default='en', values=['en', 'fr', 'de']),
                                         dict(key='turn_pause_ms', type='integer', default=768),
                                         dict(key='tts_code', type='string', default='fr', values=['fr'])]).encode()
    return pack, files


def test_a_model_only_some_settings_need_belongs_to_no_component_set():
    manifest, data = fixture()
    pack, files = with_language(data, ('fr', 'de'))
    models, _, targets = dependencies(manifest, files)
    assert [m['name'] for m in models] == ['speaker', 'language-fr']
    assert all([m['name'] for m in target['models']] == ['speaker'] for target in targets.values())
    assert conditional_downloads(models) == {'tts_language=fr': dict(files=1, bytes=9), 'tts_language=de': dict(files=1, bytes=9)}
    assert conditional_downloads([data['models']]) == {}


def test_an_unconditional_model_no_set_names_is_still_refused():
    manifest, data = fixture()
    pack, files = with_language(data)
    del pack['when']
    files['models.json'] = json.dumps([data['models'], pack]).encode()
    with pytest.raises(ValueError, match='undeployed model'):
        dependencies(manifest, files)


def test_a_component_set_cannot_name_a_conditional_model():
    manifest, data = fixture()
    data['profiles']['linux']['models'] = ['speaker', 'language-fr']
    _, files = with_language(data)
    with pytest.raises(ValueError, match='only some values of a setting'):
        dependencies(manifest, files)


@pytest.mark.parametrize('damage', ['no-settings', 'malformed-settings', 'undeclared-setting', 'not-an-enum', 'values-but-not-an-enum', 'enum-without-values',
    'value-not-declared', 'no-values', 'empty-value', 'repeated-value', 'extra-member', 'not-an-object', 'values-not-a-list'])
def test_a_model_condition_is_held_to_the_package_settings(damage):
    manifest, data = fixture()
    pack, files = with_language(data)
    when = pack['when']
    if damage == 'no-settings': del files['settings.json']
    if damage == 'malformed-settings': files['settings.json'] = b'{'
    if damage == 'undeclared-setting': when['setting'] = 'tts_dialect'
    if damage == 'not-an-enum': when['setting'] = 'turn_pause_ms'
    if damage == 'values-but-not-an-enum': when['setting'] = 'tts_code'
    if damage == 'enum-without-values':
        files['settings.json'] = json.dumps([dict(key='tts_language', type='enum', default='en')]).encode()
    if damage == 'value-not-declared': when['values'] = ['fr', 'xx']
    if damage == 'no-values': when['values'] = []
    if damage == 'empty-value':
        # refused even where the setting itself lists an empty value
        when['values'] = ['']
        files['settings.json'] = json.dumps([dict(key='tts_language', type='enum', default='en', values=['en', 'fr', ''])]).encode()
    if damage == 'repeated-value': when['values'] = ['fr', 'fr']
    if damage == 'extra-member': when['platform'] = 'linux'
    if damage == 'not-an-object': pack['when'] = 5
    if damage == 'values-not-a-list': when['values'] = 'fr'
    files['models.json'] = json.dumps([data['models'], pack]).encode()
    with pytest.raises(ValueError, match='condition|conditional'):
        dependencies(manifest, files)
