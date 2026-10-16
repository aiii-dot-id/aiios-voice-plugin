"""Authoring can advance independently of the sealed engine SDK, never unbound."""
import hashlib
import io
import tarfile

import pytest

from scripts.verify_release_sdk_compatibility import verify_sdk_source


def fixture(tmp_path, fault=None):
    source = tmp_path/'sdk'
    source.mkdir()
    raw = b'module example.invalid/source-fixture\n'
    (source/'go.mod').write_bytes(raw)
    archive = tmp_path/'sdk.tar'
    with tarfile.open(archive, 'w') as tf:
        info = tarfile.TarInfo('go.mod')
        info.size = len(raw)
        tf.addfile(info, io.BytesIO(raw))
        if fault == 'duplicate':
            tf.addfile(info, io.BytesIO(raw))
        if fault in ('symlink','traversal'):
            info = tarfile.TarInfo('alias' if fault == 'symlink' else '../escape')
            if fault == 'symlink':
                info.type = tarfile.SYMTYPE
                info.linkname = 'go.mod'
            tf.addfile(info, io.BytesIO(b''))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    return source, archive, digest


def test_authoring_tree_matches_the_exact_frozen_archive(tmp_path):
    source, archive, digest = fixture(tmp_path)
    result = verify_sdk_source(source, archive, digest)
    assert set(result) == {'go.mod'} and result['go.mod']['size'] > 0


@pytest.mark.parametrize('fault', ['digest','changed-source','extra-source','duplicate','symlink','traversal'])
def test_authoring_sdk_substitution_is_refused(tmp_path, fault):
    source, archive, digest = fixture(tmp_path, fault)
    if fault == 'digest': digest = '0'*64
    if fault == 'changed-source': (source/'go.mod').write_bytes(b'changed')
    if fault == 'extra-source': (source/'injected.go').write_bytes(b'package fixture')
    with pytest.raises(ValueError):
        verify_sdk_source(source, archive, digest)
