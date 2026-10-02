"""Assembly consumes a complete default-deny image census and per-image signatures."""
import copy

import pytest

from scripts.assemble_guided_beta_candidate import image_census, windows_authenticode

CARRIER = 'c' * 64


def staged(platform='linux'):
    files = {'lib/libaii_voice_runtime.so': dict(sha256='a' * 64, executable=True),
             'lib/libonnxruntime.so.1.24.2': dict(sha256='b' * 64, executable=True),
             'lib/libvulkan.so.1': dict(sha256='d' * 64, executable=False),
             'resources/settings.json': dict(sha256='e' * 64, executable=False)}
    carrier = 'aii-voice-t3'
    if platform == 'windows':
        files = {'bin/aii_voice_runtime.dll': dict(sha256='a' * 64, executable=False),
                 'bin/onnxruntime.dll': dict(sha256='b' * 64, executable=False)}
        carrier = 'aii-voice-t3.exe'
    profile = dict(platform=platform, files=files)
    rows = [dict(file=n, sha256=r['sha256'], findings={}, role='release_owned')
            for n, r in files.items() if not n.startswith('resources/')]
    rows.append(dict(file=carrier, sha256=CARRIER, findings={}, role='release_owned'))
    for row in rows:
        if 'onnxruntime' in row['file']:
            row.update(role='third_party', findings={'ascii:private-build-root': 21})
    return dict(carrier_sha256=CARRIER, native_image_privacy=rows), profile


def test_complete_census_is_consumed_with_third_party_roles():
    result, profile = staged()
    census = image_census(result, profile, 'linux-full')
    assert census['lib/libonnxruntime.so.1.24.2']['role'] == 'third_party'
    assert 'resources/settings.json' not in census


@pytest.mark.parametrize('fault', ['old-receipt', 'missing-image', 'missing-carrier', 'foreign-row',
    'other-bytes', 'carrier-bytes', 'owned-finding', 'secret-in-vendor', 'release-built-vendor',
    'duplicate-row', 'unknown-role'])
def test_partial_or_dirty_census_is_refused(fault):
    result, profile = staged()
    rows = result['native_image_privacy']
    if fault == 'old-receipt': result = dict(carrier_sha256=CARRIER, release_owned_image_privacy=rows)
    if fault == 'missing-image': rows[:] = [r for r in rows if r['file'] != 'lib/libvulkan.so.1']
    if fault == 'missing-carrier': rows.pop()
    if fault == 'foreign-row': rows.append(dict(file='lib/absent.so', sha256='f' * 64, findings={}, role='release_owned'))
    if fault == 'other-bytes': rows[0]['sha256'] = 'f' * 64
    if fault == 'carrier-bytes': rows[-1]['sha256'] = 'f' * 64
    if fault == 'owned-finding': rows[0]['findings'] = {'ascii:private-build-root': 1}
    if fault == 'secret-in-vendor': rows[1]['findings'] = {'ascii:provider-token': 1}
    if fault == 'release-built-vendor': rows[0]['role'] = 'third_party'
    if fault == 'duplicate-row': rows.append(copy.deepcopy(rows[0]))
    if fault == 'unknown-role': rows[0]['role'] = 'vendor'
    with pytest.raises(ValueError):
        image_census(result, profile, 'linux-full')


def windows(images):
    result, profile = staged('windows')
    result['authenticode_verified'] = True  # a bare claim, as the old receipts carried
    if images is not None:
        result['authenticode_images'] = images
    return {'windows-full': result}, {'windows-full': profile, 'linux-full': staged()[1]}


@pytest.mark.parametrize('images,expected', [
    (None, False),
    ({'bin/aii_voice_runtime.dll': 'not_verified', 'aii-voice-t3.exe': 'verified_publisher'}, False),
    ({'bin/aii_voice_runtime.dll': 'verified_publisher'}, False),
    ({'bin/aii_voice_runtime.dll': 'verified_publisher', 'aii-voice-t3.exe': 'verified_publisher',
      'bin/onnxruntime.dll': 'not_verified'}, True)])
def test_windows_claim_is_recomputed_from_release_built_images(images, expected):
    bound, profiles = windows(images)
    bound['linux-full'] = {}
    assert windows_authenticode(bound, profiles) is expected
