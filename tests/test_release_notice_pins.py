"""A notice fetched at a pinned revision is bound to its digest; a mutable page is not."""

import hashlib
import io
import json
import re

import pytest

from scripts import prepare_desktop_release_notices as notices

REVISION = re.compile(r"/[0-9a-f]{40}/")


class Recorder:
    """Stands in for the bundle: records each fetch instead of reaching the network."""

    def __init__(self, out):
        self.out, self.fetched = out, []

    def public(self, name, url, group, marker, bound=1024 * 1024, sha256=None):
        self.fetched.append((name, url, sha256))
        if name.startswith("evidence/"):  # model_notices reads each submodule record back
            path = self.out / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"sha": marker, "submodule_git_url": "recorded"}))

    def local(self, name, path, group):
        pass


def test_every_revision_pinned_notice_is_bound_to_a_digest(tmp_path):
    bundle = Recorder(tmp_path)
    notices.model_notices(bundle)
    fetched = [row for row in bundle.fetched if row[0].startswith("notices/")]
    pinned = [name for name, url, _ in fetched if REVISION.search(url)]
    assert len(pinned) == 12 and len(fetched) == 18
    for name, url, sha256 in fetched:
        if REVISION.search(url):
            assert sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", sha256), name
        else:
            assert sha256 is None, name  # a page that can change is recognized by its marker


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_pinned_notice_with_other_bytes_is_refused(tmp_path, monkeypatch):
    raw = b"MIT License\nPermission is hereby granted\n"
    monkeypatch.setattr(notices.urllib.request, "urlopen", lambda request, timeout: Response(raw))
    bundle = notices.Bundle(tmp_path / "bundle")
    with pytest.raises(ValueError, match="pinned digest"):
        bundle.public("notices/x/LICENSE", "https://example.invalid/x", "x", "Permission", sha256="0" * 64)
    digest = hashlib.sha256(raw).hexdigest()
    bundle.public("notices/x/LICENSE", "https://example.invalid/x", "x", "Permission", sha256=digest)
    assert bundle.files["notices/x/LICENSE"]["origin"]["pinned_sha256"] == digest
