"""Settings come from the built worker and bound companion, not an old template."""
import copy
import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts.assemble_guided_beta_candidate import runtime_settings, release_contract
from scripts.rebuild_native_checkpoint import write_current_settings
from tests.test_beta3_release_contract import example


def test_compiled_declaration_reaches_runtime_and_package(tmp_path):
    worker = Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']).resolve()
    runtime = tmp_path / 'runtime'
    (runtime / 'resources').mkdir(parents=True)
    # A stale parent's declaration must actually be replaced, not just ignored.
    (runtime / 'resources/settings.json').write_text('[{"key":"obsolete"}]')
    declared = write_current_settings(worker, runtime, tmp_path)
    assert (runtime / 'resources/settings.json').read_bytes() == (tmp_path / 'settings.json').read_bytes()
    row = next(s for s in declared if s['key'] == 'capture_limit_minutes')
    assert row['type'] == 'integer' and row['minimum'] == 0 and row['default'] == 30
    assert '0 = no automatic stop' in row['title']
    raw = (tmp_path / 'settings.json').read_bytes()
    archive = tmp_path / 'runtime.tar.gz'
    with tarfile.open(archive, 'w:gz') as out:
        member = tarfile.TarInfo('runtime/resources/settings.json')
        member.size = len(raw)
        out.addfile(member, io.BytesIO(raw))
    profile = {'files': {'resources/settings.json': {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}}}
    result = {'runtime_archive': {'path': str(archive)}}
    observed = runtime_settings(result, profile)
    assert observed == declared
    cfg = example()
    cfg['settings'] = observed
    before = copy.deepcopy(observed)
    release_contract(cfg)
    assert cfg['settings'] == before, 'packager changed the compiled declaration'
    assert next(s for s in cfg['settings'] if s['key'] == 'capture_limit_minutes')['scope'] == 'hearing'
    changed = copy.deepcopy(profile)
    changed['files']['resources/settings.json']['sha256'] = '0'*64
    with pytest.raises(ValueError, match='changed'):
        runtime_settings(result, changed)
    with pytest.raises(ValueError, match='missing'):
        runtime_settings(result, {'files': {}})
    cfg['settings'] = [s for s in cfg['settings'] if s['key'] != 'capture_limit_minutes']
    with pytest.raises(ValueError, match='scope'):
        release_contract(cfg)


def test_settings_description_needs_neither_model_root_nor_audio_endpoints():
    worker = Path(os.environ['AII_NATIVE_INTERRUPT_FIXTURE']).resolve()
    env = {k: v for k, v in os.environ.items() if k not in ('AII_MODELS_DIR', 'AII_AUDIO_IN_FD', 'AII_AUDIO_OUT_FD')}
    done = subprocess.run([str(worker), '--describe-settings'], env=env, capture_output=True, timeout=5, check=True)
    assert len(json.loads(done.stdout)) == 8
