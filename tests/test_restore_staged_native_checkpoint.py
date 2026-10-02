import json

import pytest

from scripts.restore_staged_native_checkpoint import restore


def test_restore_refuses_failed_stage(tmp_path):
    stage = tmp_path / 'stage.json'
    stage.write_text(json.dumps({'passed': False, 'models_in_archive': False}))
    with pytest.raises(ValueError, match='successful model-external'):
        restore(stage, tmp_path / 'missing.tar.gz', tmp_path, tmp_path / 'out', tmp_path / 'go')
    assert not (tmp_path / 'out').exists()


def test_restore_refuses_archive_not_bound_to_stage(tmp_path):
    stage = tmp_path / 'stage.json'
    stage.write_text(json.dumps({'passed': True, 'models_in_archive': False,
                                 'runtime_archive': {'sha256': '0' * 64, 'size': 1}}))
    archive = tmp_path / 'archive.tar.gz'
    archive.write_bytes(b'x')
    with pytest.raises(ValueError, match='staged archive bytes differ'):
        restore(stage, archive, tmp_path, tmp_path / 'out', tmp_path / 'go')
    assert not (tmp_path / 'out').exists()
