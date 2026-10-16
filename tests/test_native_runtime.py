"""A runtime's bound inventory: no member path leaves the runtime, and a changed, added or linked file is refused.

Four cases that were here went with what they tested: the packaged interpreter's bootstrap
(its record of loaded modules) and the Python packer's dependency closure are removed.
"""
import json

import pytest

from scripts.package_native_runtime import (
    runtime_inventory,
    safe_relative,
    sha256,
    verify,
)


@pytest.mark.parametrize(
    "name", ["", ".", "C:a", "a:stream", "/a", "../a", "a/../b", "a\\b", "a//b", "./a"]
)
def test_payload_path_refuses_escape(name):
    with pytest.raises(ValueError):
        safe_relative(name)


def test_inventory_detects_tamper_extra_and_links(tmp_path):
    code = tmp_path / "engine.py"
    code.write_text("pass\n")
    manifest = tmp_path / "voice-runtime.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "aiii.voice.native-runtime",
                "qualified": False,
                "platform": "darwin",
                "files": runtime_inventory(tmp_path),
            }
        )
    )
    digest = sha256(manifest)
    verify(tmp_path, digest)
    code.write_text("fail\n")
    with pytest.raises(ValueError, match="differs"):
        verify(tmp_path, digest)
    code.write_text("pass\n")
    extra = tmp_path / "extra.py"
    extra.write_text("pass")
    with pytest.raises(ValueError, match="differs"):
        verify(tmp_path, digest)
    extra.unlink()
    extra.symlink_to(code)
    with pytest.raises(ValueError, match="symlink"):
        verify(tmp_path, digest)
