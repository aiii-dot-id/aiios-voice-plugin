"""Source/binary binding falsifiers; no model, network, or device access."""

import hashlib
import io
import json
import tarfile

import pytest

from scripts import build_plugin_carrier as build


@pytest.fixture
def tree(tmp_path):
    source = tmp_path / build.PIN["source"]
    source.mkdir(parents=True)
    (source / "hello.go").write_text("package hello\n")
    archive = tmp_path / build.PIN["archive"]
    with tarfile.open(archive, "w") as tf:
        data = b"package hello\n"
        item = tarfile.TarInfo("hello.go")
        item.size = len(data)
        tf.addfile(item, io.BytesIO(data))
    (tmp_path / "plugin/native").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/build_plugin_carrier.py").write_text("fixture builder\n")
    pin = {**build.PIN, "archive_sha256": build.digest(archive)}
    (tmp_path / "plugin/sdk-source.json").write_text(json.dumps(pin))
    (tmp_path / "plugin/native/go.mod").write_text(
        "module fixture\nreplace github.com/aiii-dot-id/aii-plugin-sdk => ../../" + pin["source"] + "\n"
    )
    return tmp_path


def test_sealed_sdk_matches(tree):
    _, entries = build.verify_sdk(tree)
    assert entries == {"hello.go": hashlib.sha256(b"package hello\n").hexdigest()}


@pytest.mark.parametrize("damage", ["archive", "source", "extra", "missing", "link", "old_pin"])
def test_sdk_refuses_altered_or_old_inputs(tree, damage):
    source = tree / build.PIN["source"]
    if damage == "archive":
        (tree / build.PIN["archive"]).write_bytes(b"broken archive")
    elif damage == "source":
        (source / "hello.go").write_text("altered\n")
    elif damage == "extra":
        (source / "unexpected.go").write_text("package hello\n")
    elif damage == "missing":
        (source / "hello.go").unlink()
    elif damage == "link":
        (source / "linked.go").symlink_to(source / "hello.go")
    else:
        (tree / "plugin/native/go.mod").write_text(
            "replace github.com/aiii-dot-id/aii-plugin-sdk => ../../.build/aii-plugin-sdk-41eef1f\n"
        )
    with pytest.raises(ValueError):
        build.verify_sdk(tree)


@pytest.fixture
def built(tree):
    output = tree / "output"
    output.mkdir()
    artifacts = {}
    for goos, arch, race, name in build.TARGETS:
        path = output / name
        path.write_bytes(name.encode())
        artifacts[name] = {"sha256": build.digest(path), "goos": goos, "goarch": arch, "race": race}
    record = {"complete": True, "sdk_revision": build.PIN["revision"],
              "inputs": build.inputs(tree), "artifacts": artifacts}
    (output / "build.json").write_text(json.dumps(record))
    return tree, output, record


def test_matching_build_is_verified(built):
    tree, output, record = built
    assert build.verify_build(output, root=tree) == record


@pytest.mark.parametrize("damage", ["binary", "extra", "source", "incomplete", "missing_target", "wrong_target"])
def test_build_refuses_stale_partial_or_altered_output(built, damage):
    tree, output, record = built
    if damage == "binary":
        (output / "aii-voice-t3.exe").write_bytes(b"old executable")
    elif damage == "extra":
        (output / "unaccounted.exe").write_bytes(b"unexpected")
    elif damage == "source":
        (tree / "plugin/native/main.go").write_text("changed after build\n")
    elif damage == "incomplete":
        record["complete"] = False
    elif damage == "missing_target":
        record["artifacts"].pop("aii-voice-t3.exe")
    else:
        record["artifacts"]["aii-voice-t3.exe"]["goos"] = "linux"
    (output / "build.json").write_text(json.dumps(record))
    with pytest.raises(ValueError):
        build.verify_build(output, root=tree)


def test_default_selects_pinned_build_not_historical_artifact():
    assert build.carrier_path(system="win32") == build.BUILD_DIR / "aii-voice-t3.exe"
    assert build.carrier_path(system="darwin", race=True) == build.BUILD_DIR / "aii-voice-t3-race"
    assert build.carrier_path(system="linux") == build.BUILD_DIR / "aii-voice-t3-linux-amd64"
    pin = json.loads((build.ROOT / "plugin/sdk-source.json").read_text())
    assert build.BUILD_DIR.name == "native-sdk-" + pin["revision"][:7]
