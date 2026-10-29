import re
import subprocess
from pathlib import Path

import pytest

from scripts import go_toolchain

ROOT = Path(__file__).resolve().parents[1]
HISTORY = {'docs/INSTALLED_FAULT_GATE.md'}
NAMED = re.compile(r'go1\.\d+\.\d+|Go 1\.\d+\.\d+|go-version: |/usr/local/go1\.\d+')


def test_the_version_is_the_go_line_of_the_carriers_module():
    assert re.search(r'(?m)^go (\S+)$', go_toolchain.GO_MOD.read_text()).group(1) == go_toolchain.VERSION
    assert re.fullmatch(r'\d+\.\d+\.\d+', go_toolchain.VERSION)


def test_no_other_file_names_a_go_version():
    tracked = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    named = []
    for name in tracked:
        if name in HISTORY or name == 'MANIFEST.sha256' or name == 'tests/test_go_toolchain.py':
            continue
        if not name.endswith(('.py', '.md', '.yml', '.yaml', '.json', '.toml', '.sh', '.ps1', '.cmake', '.txt', '.go', '.mod')):
            continue
        text = (ROOT / name).read_text(encoding='utf-8', errors='replace')
        named += [f'{name}: {m.group(0)}' for m in NAMED.finditer(text)]
    assert not named, 'name the Go toolchain through scripts/go_toolchain.py, not here: %s' % named


def test_the_default_place_and_its_override(monkeypatch):
    monkeypatch.delenv('AII_GO', raising=False)
    assert go_toolchain.default_go() == Path(f'/usr/local/go{go_toolchain.VERSION}/bin/go')
    monkeypatch.setenv('AII_GO', '/opt/go-current/bin/go')
    assert go_toolchain.default_go() == Path('/opt/go-current/bin/go')


def test_another_version_is_refused_by_name(tmp_path):
    fake = tmp_path / 'go'
    fake.write_text('#!/bin/sh\necho "go version go0.0.1 test/fake"\n')
    fake.chmod(0o755)
    with pytest.raises(ValueError, match=re.escape(f'requires Go {go_toolchain.VERSION}')):
        go_toolchain.require(fake, 'a build')


def test_a_go_mod_without_an_exact_version_is_refused(tmp_path):
    (tmp_path / 'go.mod').write_text('module x\n\ngo 1.27\n')
    with pytest.raises(ValueError, match='exact Go version'):
        go_toolchain.read_version(tmp_path / 'go.mod')
