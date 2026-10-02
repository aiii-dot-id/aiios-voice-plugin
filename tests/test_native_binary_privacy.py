import hashlib
import json

import pytest

from scripts import check_native_binary_privacy as privacy
from scripts.check_native_binary_privacy import audit_image, scan_bytes, release_owned_image, audit_release_images


@pytest.mark.parametrize('encoding', ['ascii', 'utf-16-le'])
@pytest.mark.parametrize('value', ['/'+'Users'+'/operator/source.cc',
                                  'C:'+chr(92)+'work'+chr(92)+'private'+chr(92)+'source.cc'])
def test_private_roots_are_detected_without_echoing(encoding, value):
    findings = scan_bytes(b'\x01\x02'+value.encode(encoding)+b'\x00\x00')
    assert findings[encoding+':private-build-root'] == 1
    assert value not in str(findings)


def test_normalized_paths_are_not_private_and_explicit_prefix_is_honored():
    assert scan_bytes(b'/aii-source/source.cc\0/aii-deps/ENGINE_SOURCE/source.cc\0/aii-build/a.o') == {}
    assert scan_bytes(b'Z:/build-agent/local/a.cc', ['Z:/build-agent/local']) == {'ascii:explicit-private-prefix': 1}
    with pytest.raises(ValueError, match='empty private prefix'):
        scan_bytes(b'anything', [''])


def test_public_upstream_trace_template_is_not_a_build_path():
    template = b'/tmp/perf-metal-%d.gputrace'
    assert scan_bytes(template) == {}
    assert scan_bytes(b'private source: '+template) == {'ascii:private-build-root': 1}
    assert scan_bytes(template, ['/tmp/']) == {'ascii:explicit-private-prefix': 1}


def test_secret_checks_and_bound_image_inventory(tmp_path):
    value = ('h'+'f_'+'x'*32).encode()
    assert scan_bytes(value) == {'ascii:provider-token': 1}
    image = tmp_path/'engine.bin'
    image.write_bytes(b'/aii-source/engine.cc\0')
    result = audit_image(image)
    assert result['bytes'] == len(image.read_bytes()) and len(result['sha256']) == 64
    assert result['findings'] == {}
    with pytest.raises(ValueError, match='regular native image'):
        audit_image(tmp_path)


@pytest.mark.parametrize('name', ['lib/libggml-base.0.dylib', 'lib/libnemo_speech_asr.so',
                                 'bin/ggml-vulkan.dll', 'bin/aiii_uid_frontend.dll',
                                 'lib/libnative_pocket_resident.so', 'aii-voice-t3.exe'])
def test_prebuilt_dependency_owners_are_release_owned(name):
    assert release_owned_image(name)


@pytest.mark.parametrize('name', ['lib/libonnxruntime.so.1.24.2', 'bin/DirectML.dll',
                                 'resources/coreml-cache/model', 'lib/libcudnn.so.9'])
def test_vendor_and_data_files_have_separate_audit(name):
    assert not release_owned_image(name)


@pytest.mark.parametrize('path', ['/'+'tmp'+'/private-build/a.cc',
                                 'C:/Windows/'+'Temp'+'/private-build/a.cc',
                                 'C:/'+'aiii-voice-build'+'/private-build/a.cc'])
def test_scratch_roots_are_detected_without_explicit_prefix(path):
    assert scan_bytes(path.encode()) == {'ascii:private-build-root': 1}


def test_whole_owned_closure_fails_even_if_worker_is_clean(tmp_path):
    lib = tmp_path/'lib'
    lib.mkdir()
    carrier = tmp_path/'aii-voice-t3'
    carrier.write_bytes(b'/aii-source/carrier.cc\0')
    dependency = lib/'libggml-base.so'
    dependency.write_bytes(('/'+'tmp'+'/private-build/a.cc\0').encode())
    profile = {'files': {'lib/libggml-base.so': {}}}
    profile['files']['lib/libggml-base.so'] = row(dependency)
    with pytest.raises(ValueError, match='private strings: libggml-base.so'):
        audit_release_images(tmp_path, profile, carrier)
    dependency.write_bytes(b'/aii-source/vendor/ggml.c\0')
    profile['files']['lib/libggml-base.so'] = row(dependency)
    assert len(audit_release_images(tmp_path, profile, carrier)) == 2


def row(path, executable=False):
    raw = path.read_bytes()
    return dict(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), executable=executable)


@pytest.mark.parametrize('path', ['/'+'var/folders/xy/T/build/a.cc', '/'+'private/var/folders/xy/a.cc',
    '/'+'root/build/a.cc', '/'+'opt/build/a.cc', '/'+'mnt/c/build/a.cc', '/'+'srv/ci/a.cc',
    'C:'+chr(92)+'temp'+chr(92)+'a.cc', 'C:'+chr(92)+'proof'+chr(92)+'a.cc',
    'D:'+chr(92)+'a'+chr(92)+'voice'+chr(92)+'voice'+chr(92)+'a.cc', 'C:/actions-runner/_work/x/a.cc',
    'FAILED at "/'+'opt/build/a.cc"', '-I/'+'root/include', 'file:///'+'srv/build/a.cc'])
def test_ci_scratch_and_container_roots_are_build_paths(path):
    assert scan_bytes(path.encode()) == {'ascii:private-build-root': 1}
    assert scan_bytes(path.encode('utf-16-le')) == {'utf-16-le:private-build-root': 1}


@pytest.mark.parametrize('text', ['/usr/lib/x86_64-linux-gnu/libvulkan.so.1', '/etc/vulkan/icd.d',
    '/System/Library/Frameworks/Metal.framework/Metal', '/proc/self/exe', '/dev/null',
    '@rpath/libggml.dylib', '$ORIGIN/../lib', 'C:/Windows/System32/vulkan-1.dll',
    'https://github.com/org/repo/tree/main/root/README.md', 'https://example.com/opt/index.html',
    'third_party/opt/kernel.cc', 'src/root/main.cc', './mnt/data/x', 'groot/srv/x', 'jar:/a/b/c',
    'tree/root/children', 'x86_64:/opt-in'])
def test_ordinary_runtime_and_relative_strings_are_not_build_paths(text):
    assert scan_bytes(text.encode()) == {}


def runtime_fixture(tmp_path, images):
    files = {}
    for name, data, executable in images:
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[name] = row(path, executable)
    carrier = tmp_path/'aii-voice-t3'
    carrier.write_bytes(b'\x7fELF/aii-source/carrier.cc\0')
    return {'files': files}, carrier


LEAK = ('/'+'home/builder/voice/a.cc\0').encode()


@pytest.mark.parametrize('name,data', [
    ('lib/libggml-cpu-haswell.so', b'\x7fELF' + LEAK), ('bin/pocket_tts.dll', b'MZ' + LEAK),
    ('bin/mystery.dll', LEAK), ('lib/libunrecognised.so.3', LEAK),
    ('resources/helper', b'\x7fELF' + LEAK), ('resources/coreml-cache/model', b'\xcf\xfa\xed\xfe' + LEAK)])
def test_unrecognised_shipped_images_are_scanned_not_skipped(tmp_path, name, data):
    profile, carrier = runtime_fixture(tmp_path, [('lib/libaii_voice_runtime.so', b'\x7fELF clean', True),
                                                  (name, data, False)])
    with pytest.raises(ValueError, match='private strings: ' + name.rsplit('/', 1)[-1]):
        audit_release_images(tmp_path, profile, carrier)


def test_data_files_without_image_signature_are_not_images(tmp_path):
    profile, carrier = runtime_fixture(tmp_path, [('lib/libaii_voice_runtime.so', b'\x7fELF clean', True),
                                                  ('resources/settings.json', b'{"note": "' + LEAK[:-1] + b'"}', False)])
    rows = audit_release_images(tmp_path, profile, carrier)
    assert [r['file'] for r in rows] == ['lib/libaii_voice_runtime.so', 'aii-voice-t3']


def test_large_vendor_images_are_mapped_not_refused(tmp_path):
    # Linux Full's CUDA closure has members beyond the former 512 MiB bound;
    # default deny must scan them rather than refuse the runtime.
    assert privacy.MAX_IMAGE_BYTES >= 4 * 1024**3
    empty = tmp_path/'empty.so'
    empty.write_bytes(b'')
    assert audit_image(empty)['bytes'] == 0 and audit_image(empty)['findings'] == {}
    image = tmp_path/'large.so'
    with image.open('wb') as f:  # sparse: 64 MiB on disk only where written
        f.write(b'\x7fELF\0')
        f.truncate(64 * 2**20)
        f.seek(64 * 2**20 - 64)
        f.write(LEAK)
    row = audit_image(image)
    assert row['bytes'] == image.stat().st_size and row['findings'] == {'ascii:private-build-root': 1}
    assert row['sha256'] == hashlib.sha256(image.read_bytes()).hexdigest()


VENDOR = b'MZ\0' + ('D:'+chr(92)+'a'+chr(92)+'_work'+chr(92)+'1'+chr(92)+'s'+chr(92)+'core.cc\0').encode()


def vendor_runtime(tmp_path):
    profile, carrier = runtime_fixture(tmp_path, [('bin/aii_voice_runtime.dll', b'MZ clean', False),
                                                  ('bin/onnxruntime.dll', VENDOR, False)])
    declared = {'bin/onnxruntime.dll': dict(sha256=profile['files']['bin/onnxruntime.dll']['sha256'],
                                            distribution='onnxruntime 1.24.4 (NuGet)')}
    return profile, carrier, declared


def test_exact_declared_third_party_bytes_report_vendor_build_roots(tmp_path):
    profile, carrier, declared = vendor_runtime(tmp_path)
    with pytest.raises(ValueError, match='private strings: onnxruntime.dll'):
        audit_release_images(tmp_path, profile, carrier)
    rows = {r['file']: r for r in audit_release_images(tmp_path, profile, carrier, declared)}
    assert rows['bin/onnxruntime.dll']['role'] == 'third_party'
    assert rows['bin/onnxruntime.dll']['findings'] == {'ascii:private-build-root': 1}
    assert rows['bin/aii_voice_runtime.dll']['role'] == 'release_owned'


@pytest.mark.parametrize('fault', ['other-bytes', 'absent', 'release-built', 'private-prefix', 'secret'])
def test_third_party_declaration_cannot_hide_our_strings(tmp_path, fault):
    profile, carrier, declared = vendor_runtime(tmp_path)
    prefixes = ()
    if fault == 'other-bytes': declared['bin/onnxruntime.dll']['sha256'] = 'f' * 64
    if fault == 'absent': declared['bin/absent.dll'] = dict(sha256='f' * 64, distribution='none')
    if fault == 'release-built':
        declared['bin/aii_voice_runtime.dll'] = dict(sha256=profile['files']['bin/aii_voice_runtime.dll']['sha256'],
                                                     distribution='claimed vendor')
    if fault == 'private-prefix': prefixes = ('D:/a/_work',)
    if fault == 'secret':
        (tmp_path/'bin/onnxruntime.dll').write_bytes(VENDOR + ('h'+'f_'+'x'*32).encode())
        profile['files']['bin/onnxruntime.dll'] = row(tmp_path/'bin/onnxruntime.dll')
        declared['bin/onnxruntime.dll']['sha256'] = profile['files']['bin/onnxruntime.dll']['sha256']
    with pytest.raises(ValueError):
        audit_release_images(tmp_path, profile, carrier, declared, prefixes)


def test_third_party_declaration_is_bound_by_its_digest(tmp_path):
    record = dict(schema='aiii.voice.third-party-images.v1',
                  images={'bin/onnxruntime.dll': dict(sha256='a' * 64, distribution='onnxruntime 1.24.4')})
    path = tmp_path/'third-party.json'
    path.write_text(json.dumps(record))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert privacy.third_party_images(path, digest) == record['images']
    assert privacy.third_party_images(None, None) == {}
    for bad in (dict(record, schema='other'), dict(record, extra=True),
                dict(record, images={'bin/x.dll': dict(sha256='a' * 64)}),
                dict(record, images={'bin/x.dll': dict(sha256='A' * 64, distribution='x')})):
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            privacy.third_party_images(path, hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match='changed'):
        privacy.third_party_images(path, digest)
    with pytest.raises(ValueError):
        privacy.third_party_images(None, digest)


def package_files():
    return {'models.json': json.dumps([dict(url='https://github.com/aiii-dot-id/voice/model.bin')]).encode(),
            'notices/INDEX.json': json.dumps(dict(libraries=[dict(proof='wheel RECORD')])).encode(),
            'notices/vendor/LICENSE': ('Copyright; see /'+'home/upstream/README').encode(),
            'payloads/macos-full': b'\xcf\xfa\xed\xfe carrier scanned at staging'}


def test_package_metadata_is_scanned_but_upstream_texts_and_carriers_are_not(tmp_path):
    manifest = dict(id='id.aiii.voice', version='0.1.0-beta.7')
    assert privacy.package_metadata(manifest, package_files())['members'] == 3


@pytest.mark.parametrize('member', ['manifest', 'notices/INDEX.json', 'models.json', 'notices/uid/UID-REPLACEMENT.json'])
def test_package_metadata_with_a_private_path_is_refused(member):
    manifest, files = dict(id='id.aiii.voice'), package_files()
    leak = '/'+'Volumes/builder/evidence/notice'
    if member == 'manifest': manifest['description'] = leak
    else: files[member] = json.dumps(dict(local_path=leak)).encode()
    with pytest.raises(ValueError, match='package metadata contains private strings'):
        privacy.package_metadata(manifest, files)
