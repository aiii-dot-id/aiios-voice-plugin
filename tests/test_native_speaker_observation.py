"""Compile the production event mapper; every mutation must compile then fail."""
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'runtime/native/session'
JSON = ROOT / 'runtime/native/vendor/cjson'

@pytest.fixture(scope='module')
def json_object(tmp_path_factory):
    path = tmp_path_factory.mktemp('uid-json') / 'json.o'
    subprocess.run(['cc', '-c', str(JSON / 'cJSON.c'), '-o', str(path)], check=True)
    return path

@pytest.mark.parametrize('damage', [None, 'omitted', 'label', 'uncertain'])
def test_stable_id_wire_contract(tmp_path, json_object, damage):
    raw = (SOURCE / 'speaker_observation.h').read_text()
    original = 'put(data, "speaker_id", string(id));'
    changes = {
        'omitted': '(void)id;',
        'label': 'put(data, "speaker_id", clone(field(detail.get(), "label")));',
        'uncertain': 'put(data, "speaker_id", clone(field(detail.get(), "speaker_id")));',
    }
    if damage:
        assert raw.count(original) == 1
        raw = raw.replace(original, changes[damage])
    (tmp_path / 'speaker_observation.h').write_text(raw)
    (tmp_path / 'test.cpp').write_bytes((SOURCE / 'speaker_observation_test.cpp').read_bytes())
    binary = tmp_path / 'test'
    build = subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-I' + str(SOURCE), '-I' + str(JSON), str(tmp_path / 'test.cpp'),
                            str(json_object), '-o', str(binary)], capture_output=True, text=True, timeout=30)
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
    if damage:
        assert result.returncode != 0
        expected = {'omitted': 'string required', 'label': 'stable enrolled ID missing',
                    'uncertain': 'uncertain match claimed a speaker identity'}[damage]
        assert expected in result.stderr
    else:
        assert result.returncode == 0, result.stderr

