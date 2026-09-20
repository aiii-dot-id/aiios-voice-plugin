from pathlib import Path

import pytest

from scripts.rebuild_native_checkpoint import optional_libraries


@pytest.mark.parametrize('platform,suffix,prefix', [
    ('darwin', '.dylib', 'lib'), ('linux', '.so', 'lib'), ('windows', '.dll', '')])
def test_replacements_name_existing_components_only(tmp_path, platform, suffix, prefix):
    stems = ('aiii_uid_frontend', 'aii_native_uid', 'native_pocket_resident')
    names = ['lib/'+prefix+stem+suffix for stem in stems]
    paths = [tmp_path/(stem+suffix) for stem in stems]
    for path in paths:
        path.write_bytes(b'explicitly bound image fixture')
    profile = dict(platform=platform, files=dict.fromkeys(names+['lib/vendor'+suffix]))
    assert optional_libraries(profile) == {}
    assert optional_libraries(profile, uid_frontend=paths[0], uid=paths[1], tts=paths[2]) == dict(zip(names, paths))
    assert optional_libraries(profile, uid_frontend=paths[0]) == {names[0]: paths[0]}
    del profile['files'][names[0]]
    with pytest.raises(ValueError, match='layout differs'):
        optional_libraries(profile, uid_frontend=paths[0])


def test_ambiguous_target_and_missing_source_refused(tmp_path):
    source = tmp_path/'fixture'
    profile = dict(platform='linux', files={'lib/aiii_uid_frontend.so': {}, 'lib/libaiii_uid_frontend.so': {}})
    with pytest.raises(ValueError, match='regular library'):
        optional_libraries(profile, uid_frontend=source)
    source.write_bytes(b'fixture')
    with pytest.raises(ValueError, match='layout differs'):
        optional_libraries(profile, uid_frontend=source)


def test_symlink_and_unsafe_destination_refused(tmp_path):
    source = tmp_path/'fixture'
    source.write_bytes(b'fixture')
    link = tmp_path/'link'
    link.symlink_to(source)
    profile = dict(platform='linux', files={'lib/libaiii_uid_frontend.so': {}})
    with pytest.raises(ValueError, match='regular library'):
        optional_libraries(profile, uid_frontend=link)
    profile['files'] = {'../libaiii_uid_frontend.so': {}}
    with pytest.raises(ValueError):
        optional_libraries(profile, uid_frontend=source)
