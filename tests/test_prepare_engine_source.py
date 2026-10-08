"""The engine source a build is given is made from an upstream checkout by digests alone, or not at all."""
import hashlib
from pathlib import Path

import pytest

from scripts import prepare_engine_source as tool

HEAD = "// one\n// two\n// three\n// four\n"
BLOCK = ("\n        // These tensors escape compute_backend_graph: kept.\n"
         "        retain(attention_mask_);\n")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def entry(name, path, upstream, built, more=""):
    return f"{name}\n  Upstream: {path}\n  Upstream sha256: {sha(upstream)}\n  As built sha256: {sha(built)}\n  Changed: a line.{more}\n\n"


@pytest.fixture
def made(tmp_path):
    """A small upstream, and a tree's two directories stating what replaces which of its files."""
    files = {"inc/a.h": "int a;\n", "src/mimi_decoder.cpp": "decoder();\n", "src/acoustic_model.cpp": "acoustic();\n",
             "src/other.cpp": "other();\n"}
    upstream = tmp_path / "upstream"
    for path, text in files.items():
        (upstream / path).parent.mkdir(parents=True, exist_ok=True)
        (upstream / path).write_text(text)
    (upstream / ".git").mkdir()
    (upstream / ".git" / "HEAD").write_text("ref\n")
    engine, windows = tmp_path / "engine", tmp_path / "windows"
    engine.mkdir(); windows.mkdir()
    a, engine_decoder = "int a; int b;\n", "decoder(config());\n"
    decoder, acoustic = engine_decoder + BLOCK, "acoustic(); refuse();\n"
    (engine / "a.h").write_text(HEAD + a)
    (engine / "NOTICE").write_text("Changes\n\n" + entry("a.h", "inc/a.h", files["inc/a.h"], a))
    (windows / "mimi_decoder.cpp").write_text(HEAD + decoder)
    (windows / "acoustic_model.cpp").write_text(HEAD + acoustic)
    (windows / "NOTICE").write_text("Overrides\n\n"
        + entry("acoustic_model.cpp", "src/acoustic_model.cpp", files["src/acoustic_model.cpp"], acoustic)
        + entry("mimi_decoder.cpp", "src/mimi_decoder.cpp", files["src/mimi_decoder.cpp"], decoder,
                f"\n  The engine directory holds this file without the block (sha256\n  {sha(engine_decoder)})."))
    return dict(upstream=upstream, engine=engine, windows=windows, files=files, a=a, decoder=decoder,
                engine_decoder=engine_decoder, acoustic=acoustic, out=tmp_path / "out")


def read(made, path):
    return (made["out"] / path).read_text()


def test_the_windows_layout_holds_the_replaced_files_and_the_decoder_without_its_block(made):
    replaced = tool.prepare(made["upstream"], "windows", made["out"], made["engine"], made["windows"])
    assert sorted(replaced) == ["inc/a.h", "src/mimi_decoder.cpp"]
    assert read(made, "inc/a.h") == made["a"] and read(made, "src/mimi_decoder.cpp") == made["engine_decoder"]
    # What the Windows recipe compiles from its own directory is left as upstream has it, and so is the rest.
    assert read(made, "src/acoustic_model.cpp") == made["files"]["src/acoustic_model.cpp"]
    assert read(made, "src/other.cpp") == made["files"]["src/other.cpp"] and not (made["out"] / ".git").exists()


def test_the_macos_layout_holds_both_replaced_files_whole(made):
    replaced = tool.prepare(made["upstream"], "macos", made["out"], made["engine"], made["windows"])
    assert sorted(replaced) == ["inc/a.h", "src/acoustic_model.cpp", "src/mimi_decoder.cpp"]
    assert read(made, "src/mimi_decoder.cpp") == made["decoder"] and read(made, "src/acoustic_model.cpp") == made["acoustic"]
    assert read(made, "inc/a.h") == made["a"]


@pytest.mark.parametrize("fault", ["upstream-file-changed", "upstream-file-absent", "tree-file-changed", "output-exists",
                                   "decoder-block-changed", "notice-without-a-digest", "another-layout"])
def test_nothing_is_written_where_a_digest_does_not_hold(made, fault):
    layout = "windows"
    if fault == "upstream-file-changed": (made["upstream"] / "inc/a.h").write_text("int a; // edited\n")
    if fault == "upstream-file-absent": (made["upstream"] / "src/mimi_decoder.cpp").unlink()
    if fault == "tree-file-changed": (made["engine"] / "a.h").write_text(HEAD + "int a; int c;\n")
    if fault == "output-exists": made["out"].mkdir()
    if fault == "decoder-block-changed":
        # The tree's decoder and its as-built digest agree, and what is left without the block is not the
        # file the notice states for the engine directory.
        other = made["engine_decoder"].replace("config", "other") + BLOCK
        (made["windows"] / "mimi_decoder.cpp").write_text(HEAD + other)
        notice = made["windows"] / "NOTICE"
        notice.write_text(notice.read_text().replace(sha(made["decoder"]), sha(other)))
    if fault == "notice-without-a-digest":
        notice = made["engine"] / "NOTICE"
        notice.write_text(notice.read_text().replace("As built sha256: ", "As built: "))
    if fault == "another-layout": layout = "linux"
    existed = made["out"].exists()
    with pytest.raises(ValueError):
        tool.prepare(made["upstream"], layout, made["out"], made["engine"], made["windows"])
    assert made["out"].exists() == existed and (not existed or not any(made["out"].iterdir()))


def test_the_trees_own_files_hold_for_both_layouts():
    """Without an upstream checkout: every file of the tree is the one its notice states as built, and the
    decoder without its added block is the file the notice states for the Windows build's engine directory."""
    windows, macos = tool.replacements("windows"), tool.replacements("macos")
    assert len(windows) == 6 and len(macos) == 7 and set(windows) < set(macos)
    assert all(path.startswith(("external/ggml/", "include/engine/", "src/")) for path in macos)
    assert sorted(set(macos) - set(windows)) == ["src/models/pocket_tts/acoustic_model.cpp"]
    decoder = "src/models/pocket_tts/mimi_decoder.cpp"
    whole, without = macos[decoder][1], windows[decoder][1]
    block = whole[whole.find(tool.BLOCK_BEGINS):whole.find(tool.BLOCK_ENDS) + len(tool.BLOCK_ENDS)]
    assert block.count(b"\n") == 17 and whole.replace(block, b"") == without


def test_the_linux_layout_is_the_five_the_three_kept_and_the_three_derived():
    linux, macos = tool.replacements("linux"), tool.replacements("macos")
    derived = sorted(path for path, (_, data, _) in linux.items() if data is None)
    assert len(linux) == 11 and derived == sorted(tool.path_shim().PINS)
    assert sorted(set(linux) - set(macos)) == sorted(derived + ["src/models/pocket_tts/session.cpp"])
    # The decoder and the acoustic model of the kept Linux source are not the other two systems' files.
    assert all(linux[path][1] != macos[path][1] for path in
               ("src/models/pocket_tts/mimi_decoder.cpp", "src/models/pocket_tts/acoustic_model.cpp"))
    # The five are the same bytes on every system.
    assert all(linux[path][1] == macos[path][1] for path in tool.replacements("windows")
               if path != "src/models/pocket_tts/mimi_decoder.cpp")


def test_the_linux_layout_writes_nothing_from_an_upstream_that_lacks_a_file(tmp_path):
    """The three files the derivation starts from are in the tree; an upstream that holds only those is refused."""
    upstream = tool.path_shim().UPSTREAM
    with pytest.raises(ValueError):
        tool.prepare(upstream, "linux", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_a_refusal_at_the_command_writes_nothing_and_exits_2(made, capsys):
    (made["upstream"] / "external/ggml/src/ggml-cpu").mkdir(parents=True)
    assert tool.main(["--upstream", str(made["upstream"]), "--layout", "macos", "--output", str(made["out"])]) == 2
    assert not made["out"].exists() and "refused:" in capsys.readouterr().err
