import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'runtime/native/session'


@pytest.mark.parametrize('mutation', [None, 'reset', 'overwrite', 'disabled', 'stale'])
def test_compiled_phase_trace_owns_observation(tmp_path, mutation):
    header = (SOURCE / 'tts_phase_trace.h').read_text()
    changes = {
        'reset': ('value.size(), 0)', 'value.size(), 1)'),
        'overwrite': ('!active_ || first_ || !samples', '!active_ || !samples'),
        'disabled': ('if (!enabled_) return;', 'if (false) return;'),
        'stale': ('first_counters_ = {};', '/* stale counter mutation */'),
    }
    if mutation:
        before, after = changes[mutation]
        assert before in header
        header = header.replace(before, after)
    (tmp_path / 'tts_phase_trace.h').write_text(header)
    (tmp_path / 'test.cpp').write_bytes((SOURCE / 'tts_phase_trace_test.cpp').read_bytes())
    binary = tmp_path / 'test'
    built = subprocess.run(['c++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(tmp_path / 'test.cpp'), '-o', str(binary)], capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
    if mutation:
        assert result.returncode != 0
        message = {'reset': 'trace reset global counters', 'overwrite': 'first output snapshot was overwritten',
                   'disabled': 'disabled trace touched model or output', 'stale': 'cancelled generation inherited counters'}
        assert message[mutation] in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        assert len(rows) == 2 and rows[0]['begin_ns'] <= rows[0]['start_ns'] <= rows[0]['first_ns'] <= rows[0]['retired_ns']
        assert rows[1]['generation'] == 12 and rows[1]['first_ns'] == rows[1]['first_samples'] == 0


def test_native_placement_does_not_touch_cancel_or_publish_diagnostics_as_audio():
    source = (SOURCE / 'native_models.cpp').read_text().split('struct Pocket final:', 1)[1].split('struct NativeModels::Impl', 1)[0]
    assert 'phase_trace' not in source.split('void cancel(uint64_t id)', 1)[1]
    assert source.index('phase_trace.begin') < source.index('const auto rc=nv_start') < source.index('phase_trace.started')
    assert source.index('if(rc==-2) throw Cancelled("synthesis cancelled during inference")') < source.index('phase_trace.audio(n)')
