import subprocess
import sys
import pytest
from scripts.freeze_common_native_desktop import tool_output


def test_native_describe_ignores_windows_ansi_codepage(monkeypatch):
    monkeypatch.setattr(subprocess, '_text_encoding', lambda: 'cp1252')
    # Real pipe, bytes from the child, with a deliberately incompatible locale.
    command = [sys.executable, '-c', "import sys;sys.stdout.buffer.write(bytes.fromhex('c389706f6e696e65'))"]
    assert tool_output(command) == 'Éponine'
    # The former locale-driven implementation must actually produce the harm.
    assert subprocess.check_output(command, text=True) == 'Ã‰ponine'


def test_invalid_native_utf8_is_refused_not_replaced():
    with pytest.raises(UnicodeDecodeError):
        tool_output([sys.executable, '-c', "import sys;sys.stdout.buffer.write(b'\\xff')"])
