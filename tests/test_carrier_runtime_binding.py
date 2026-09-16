from pathlib import Path
import pytest
from scripts.audit_native_desktop_family import executable_binding

ROOT = Path(__file__).resolve().parents[1]
GOOD = ROOT / 'deliverables/checkpoints/desktop-beta-unified-20260914-r5'
DEV = ROOT / '.build/native-carrier-desktop-beta-20260914-r1'
TARGETS = [('macos-arm64-native', 'aii-voice-t3'),
           ('linux-x86_64-native', 'aii-voice-t3-linux-amd64'),
           ('windows-x86_64-native', 'aii-voice-t3.exe')]


@pytest.mark.parametrize('variant,dev', TARGETS)
@pytest.mark.parametrize('damage', [None, 'unbound', 'wrong-binding', 'digest-appended-to-unbound'])
def test_actual_executable_binding_not_a_search_hit(variant, dev, damage):
    from scripts.audit_native_desktop_family import digest
    expected = digest((GOOD / 'bound' / variant / 'runtime/voice-runtime.json').read_bytes())
    raw = (GOOD / 'payloads' / variant).read_bytes()
    if damage == 'wrong-binding':
        expected = 'f' * 64
    elif damage in ('unbound', 'digest-appended-to-unbound'):
        raw = (DEV / dev).read_bytes()
        if damage.endswith('unbound') and damage.startswith('digest'):
            raw += expected.encode()
    if damage:
        with pytest.raises(AssertionError, match='compiled runtime binding'):
            executable_binding(raw, expected)
    else:
        assert executable_binding(raw, expected) == expected
