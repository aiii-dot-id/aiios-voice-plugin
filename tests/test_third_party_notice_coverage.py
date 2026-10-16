"""Third-party notices and shipped binaries bind in both directions."""
import copy

import pytest

from scripts import assemble_guided_beta_candidate as assembly
from scripts.export_mossformer2_separator import CHECKPOINT_SHA256, UPSTREAM_REVISION


def image(sha, role='release_owned'):
    return dict(sha256=sha, role=role, findings={})


def fixture():
    profiles = {
        'windows-full': dict(platform='windows', files={
            'bin/aii_voice_runtime.dll': dict(sha256='a' * 64), 'bin/onnxruntime.dll': dict(sha256='b' * 64),
            'bin/vulkan-1.dll': dict(sha256='c' * 64), 'resources/settings.json': dict(sha256='d' * 64)}),
        'windows-small': dict(platform='windows', files={
            'bin/aii_voice_runtime.dll': dict(sha256='a' * 64), 'bin/onnxruntime.dll': dict(sha256='b' * 64)}),
        'linux-full': dict(platform='linux', files={
            'lib/libaii_voice_runtime.so': dict(sha256='e' * 64), 'lib/libcudart.so.12': dict(sha256='f' * 64)}),
    }
    census = {
        'windows-full': {'bin/aii_voice_runtime.dll': image('a' * 64), 'bin/onnxruntime.dll': image('b' * 64, 'third_party'),
                         'bin/vulkan-1.dll': image('c' * 64), 'aii-voice-t3.exe': image('0' * 64)},
        'windows-small': {'bin/aii_voice_runtime.dll': image('a' * 64), 'bin/onnxruntime.dll': image('b' * 64, 'third_party'),
                          'aii-voice-t3.exe': image('0' * 64)},
        'linux-full': {'lib/libaii_voice_runtime.so': image('e' * 64), 'lib/libcudart.so.12': image('f' * 64),
                       'aii-voice-t3': image('1' * 64)},
    }
    index = dict(libraries=[
        dict(component='onnxruntime', platform='windows', shipped_sha256='b' * 64, notice_group='onnxruntime-windows'),
        dict(component='vulkan-1.dll', platform='windows', variants=['windows-full'], shipped_sha256='c' * 64,
             notice_group='vulkan-loader'),
        dict(component='libcudart.so.12', platform='linux', shipped_sha256='f' * 64, notice_group='cuda-runtime')])
    notices = [dict(path='notices/' + g + '/LICENSE', sha256='9' * 64, size=10)
               for g in ('onnxruntime-windows', 'vulkan-loader', 'cuda-runtime')]
    return index, profiles, census, notices


def test_every_third_party_image_and_notice_bind_each_other():
    assembly.notice_coverage(*fixture())


@pytest.mark.parametrize('fault', ['vulkan-without-notice', 'cuda-without-notice', 'unknown-clean-image',
    'notice-only-on-other-set', 'notice-without-text', 'notice-group-escape', 'notice-without-shipped-bytes',
    'notice-names-absent-set', 'no-library-inventory', 'empty-notice-text'])
def test_one_way_coverage_is_not_enough(fault):
    index, profiles, census, notices = fixture()
    libraries = {lib['component']: lib for lib in index['libraries']}
    if fault == 'vulkan-without-notice': index['libraries'].remove(libraries['vulkan-1.dll'])
    if fault == 'cuda-without-notice': index['libraries'].remove(libraries['libcudart.so.12'])
    if fault == 'unknown-clean-image':
        # Scanned clean and undeclared is not the same as built by this release.
        profiles['windows-small']['files']['bin/helper.dll'] = dict(sha256='7' * 64)
        census['windows-small']['bin/helper.dll'] = image('7' * 64)
    if fault == 'notice-only-on-other-set':
        profiles['windows-small']['files']['bin/vulkan-1.dll'] = dict(sha256='c' * 64)
        census['windows-small']['bin/vulkan-1.dll'] = image('c' * 64)
    if fault == 'notice-without-text': notices[:] = [n for n in notices if 'vulkan-loader' not in n['path']]
    if fault == 'notice-group-escape': libraries['vulkan-1.dll']['notice_group'] = '../vulkan-loader'
    if fault == 'notice-without-shipped-bytes': libraries['onnxruntime']['shipped_sha256'] = '8' * 64
    if fault == 'notice-names-absent-set': libraries['vulkan-1.dll']['variants'] = ['windows-absent']
    if fault == 'no-library-inventory': index.pop('libraries')
    if fault == 'empty-notice-text': notices[1]['size'] = 0
    with pytest.raises(ValueError):
        assembly.notice_coverage(index, profiles, census, notices)


def separator_fixture():
    models = [dict(path='stt/encoder.onnx', sha256='1' * 64, size=11),
              dict(path='stt/separator.onnx', sha256='2' * 64, size=12),
              dict(path='stt-small/separator.onnx', sha256='3' * 64, size=13)]
    cfg = dict(models=models)
    separators = {m['path']: dict(sha256=m['sha256'], bytes=m['size']) for m in models[1:]}
    index = dict(models=[dict(path=m['path'], sha256=m['sha256'], bytes=m['size'],
                              notice_group='native-multitalker' if m is models[0] else 'separator-upstream')
                         for m in models],
                 separator_provenance=dict(source_revision=UPSTREAM_REVISION, checkpoint_sha256=CHECKPOINT_SHA256,
                                           repository='supplied by the release owner', license='supplied by the release owner',
                                           notice_group='separator-upstream', models=separators))
    notices = [dict(path='notices/separator-upstream/LICENSE', sha256='4' * 64, size=20),
               dict(path='notices/native-multitalker/NOTICE', sha256='5' * 64, size=20)]
    return index, cfg, notices


def test_separator_is_attributed_to_its_pinned_upstream():
    index, cfg, notices = separator_fixture()
    record = assembly.separator_attribution(index, cfg, notices)
    assert record['source_revision'] == UPSTREAM_REVISION and record['checkpoint_sha256'] == CHECKPOINT_SHA256
    assert assembly.separator_attribution(dict(models=[]), dict(models=cfg['models'][:1]), notices) is None


@pytest.mark.parametrize('fault', ['nvidia-group', 'row-in-nvidia-group', 'no-provenance', 'other-revision',
    'other-checkpoint', 'model-census', 'no-license', 'no-repository', 'no-notice-text', 'row-bytes'])
def test_separator_cannot_inherit_nvidia_terms_or_unpinned_provenance(fault):
    index, cfg, notices = separator_fixture()
    record = index['separator_provenance']
    if fault == 'nvidia-group':  # consistently filed under NVIDIA's terms
        record['notice_group'] = 'native-multitalker'
        for row in index['models'][1:]: row['notice_group'] = 'native-multitalker'
    if fault == 'row-in-nvidia-group': index['models'][1]['notice_group'] = 'native-multitalker'
    if fault == 'no-provenance': index.pop('separator_provenance')
    if fault == 'other-revision': record['source_revision'] = '0' * 40
    if fault == 'other-checkpoint': record['checkpoint_sha256'] = '0' * 64
    if fault == 'model-census': record['models'].pop('stt-small/separator.onnx')
    if fault == 'no-license': record['license'] = ' '
    if fault == 'no-repository': record.pop('repository')
    if fault == 'no-notice-text': notices.pop(0)
    if fault == 'row-bytes': index['models'][2]['bytes'] = 99
    before = copy.deepcopy(index)
    with pytest.raises(ValueError, match='separator'):
        assembly.separator_attribution(index, cfg, notices)
    assert index == before
