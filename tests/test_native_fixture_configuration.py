"""The topology fixture configures from shipped source, never external cJSON."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('corrupt', [False, True])
def test_fixture_uses_bound_vendored_parser(tmp_path, corrupt):
    source = tmp_path / 'source'
    shutil.copytree(ROOT / 'runtime', source / 'runtime')
    if corrupt:
        parser = source / 'runtime/native/vendor/cjson/cJSON.c'
        parser.write_bytes(parser.read_bytes() + b'\n/* changed */\n')
    run = subprocess.run([
        'cmake', '-S', str(source / 'runtime/native/session'),
        '-B', str(tmp_path / 'build'), '-DAII_WORKER_FIXTURE=ON',
        '-DCMAKE_BUILD_TYPE=Release',
    ], capture_output=True, text=True, timeout=60)
    output = run.stdout + run.stderr
    if corrupt:
        assert run.returncode != 0, output
        assert 'Bound cJSON source changed' in output, output
    else:
        assert run.returncode == 0, output
