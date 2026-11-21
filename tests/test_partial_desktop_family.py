import copy
import json

import pytest

from scripts import assemble_guided_beta_candidate as assembly
from scripts.assemble_guided_beta_candidate import candidate_inputs, release_contract
from tests.test_beta3_release_contract import bound_input_fixture, example, measured
from tests.test_third_party_notice_coverage import fixture as notice_fixture


def only(tmp_path, platforms, desktops=None):
    manifest, declaration = bound_input_fixture(tmp_path)
    keep = {v: row for v, row in declaration['variants'].items() if row['platform'] in platforms}
    declaration = dict(variants=keep, variant_preference=list(keep))
    if desktops is not None:
        declaration['desktops'] = desktops
    manifest.write_text(json.dumps(declaration))
    return manifest


def test_inputs_that_name_no_desktops_still_need_all_three(tmp_path):
    with pytest.raises(ValueError, match=r'exactly the desktops named: linux, macos, windows \(inputs that name none'):
        candidate_inputs(only(tmp_path, {'macos'}))


def test_inputs_that_name_their_desktops_bind_exactly_those(tmp_path):
    bindings, order = candidate_inputs(only(tmp_path, {'macos'}, ['macos']))
    assert {row['platform'] for row in bindings.values()} == {'macos'} and order == list(bindings)


@pytest.mark.parametrize('desktops, bound', [
    (['macos'], {'macos', 'windows'}),
    (['macos', 'linux'], {'macos'}),
    ([], {'macos'}),
    (['macos', 'macos'], {'macos'}),
    (['android'], {'macos'}),
    ('macos', {'macos'}),
])
def test_a_named_desktop_set_is_held_both_ways(tmp_path, desktops, bound):
    with pytest.raises(ValueError):
        candidate_inputs(only(tmp_path, bound, desktops))


def test_an_unknown_key_beside_the_desktops_is_still_refused(tmp_path):
    manifest = only(tmp_path, {'macos'}, ['macos'])
    declaration = json.loads(manifest.read_text()); declaration['platforms'] = ['macos']
    manifest.write_text(json.dumps(declaration))
    with pytest.raises(ValueError, match='explicit variants and variant_preference'):
        candidate_inputs(manifest)


def test_the_release_contract_takes_a_family_for_one_desktop_and_refuses_an_unknown_one():
    cfg = example()
    cfg['variants'] = [v for v in cfg['variants'] if v['platform'] == 'macos']
    cfg['variant_preference'] = [v['variant_id'] for v in cfg['variants']]
    release_contract(cfg, '1.2.3', measured(cfg))
    odd = copy.deepcopy(cfg); odd['variants'][0]['platform'] = 'android'
    with pytest.raises(ValueError, match='every desktop they are for'):
        release_contract(odd, '1.2.3', measured(odd))
    empty = copy.deepcopy(cfg); empty['variants'] = []; empty['variant_preference'] = []
    with pytest.raises(ValueError):
        release_contract(empty, '1.2.3', measured(empty))


def test_the_description_names_the_desktops_and_reads_as_before_for_all_three():
    assert assembly.desktop_words(assembly.PLATFORMS) == 'macOS, Windows and Ubuntu'
    assert assembly.desktop_words({'macos'}) == 'macOS'
    assert assembly.desktop_words({'macos', 'linux'}) == 'macOS and Ubuntu'


def test_notices_of_a_desktop_the_package_is_not_for_are_not_bound_and_the_rest_still_are():
    index, profiles, census, notices = notice_fixture()
    for v in [v for v, p in profiles.items() if p['platform'] == 'linux']:
        profiles.pop(v); census.pop(v)
    with pytest.raises(ValueError, match='unbound composition'):
        assembly.notice_coverage(index, profiles, census, notices)
    assembly.notice_coverage(index, profiles, census, notices, {'windows'})
    index['libraries'] = [lib for lib in index['libraries'] if lib['component'] != 'vulkan-1.dll']
    with pytest.raises(ValueError, match='no bound third-party notice'):
        assembly.notice_coverage(index, profiles, census, notices, {'windows'})


@pytest.mark.parametrize('label', [None, 'darwin', 'android', ''])
def test_a_library_row_naming_no_known_desktop_is_refused_before_any_is_skipped(label):
    for desktops in (assembly.PLATFORMS, {'windows'}):
        index, profiles, census, notices = notice_fixture()
        row = dict(component='stray', shipped_sha256='e' * 64, notice_group='cuda-runtime')
        if label is not None:
            row['platform'] = label
        index['libraries'].append(row)
        with pytest.raises(ValueError, match='names no desktop: stray'):
            assembly.notice_coverage(index, profiles, census, notices, desktops)


def test_main_claims_windows_authenticode_only_for_a_family_with_windows():
    import inspect
    main = ' '.join(inspect.getsource(assembly.main).split())
    assert ("windows_authenticode_verified=(windows_authenticode(bound, profiles) if 'windows' in desktops "
            "else 'not_applicable: the package has no Windows set')") in main
    assert "required_before_release=([] if 'windows' not in desktops or windows_authenticode(bound, profiles)" in main
    assert main.index("index['libraries'] = [lib for lib in index['libraries'] if lib['platform'] in desktops]") \
        > main.index('notice_coverage(index, profiles, census, notice_rows, desktops)')
    assert main.index("index['libraries'] = [lib for") < main.index("emit(author/'notices/INDEX.json',index)")
