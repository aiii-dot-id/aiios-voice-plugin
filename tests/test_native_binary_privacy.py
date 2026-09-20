import pytest

from scripts.check_native_binary_privacy import audit_image, scan_bytes


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
