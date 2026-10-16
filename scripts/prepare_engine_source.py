"""Make the engine source a speaking-library build is given, from an upstream checkout of audio.cpp.

The builds of the speaking library (runtime/native_pocket) take a directory ENGINE_SOURCE. It is not the
upstream revision as it stands: some of its files are replaced by files kept in this tree, each stated in
a NOTICE beside it with the sha256 of the upstream file it replaces and of the file as it was built.
This writes that directory, and writes nothing unless every digest holds:

  every upstream file a notice names is the upstream's (its "Upstream sha256");
  every file put in its place is the one that was built (the tree's file without its four-line head
  comment is its "As built sha256").

Three layouts. Two are as the Windows and the macOS builds were made; the third is the engine source kept
from the time of the Linux library's build, which no record ties to that build (engine_overrides_linux/NOTICE):

  windows  the five files of runtime/native_pocket/engine_overrides, and the decoder as that build's engine
           directory held it: the tree's mimi_decoder.cpp without its one added block. The Windows recipe
           compiles the two files of windows_resident/overrides itself, from where they are.
  macos    the five, and the two files of windows_resident/overrides in place of the engine's own. The
           portable recipe requires metal-library-beside.patch applied in this directory as well; that is
           one command, which this prints and does not run.
  linux    the five, the three files of engine_overrides_linux, and the three files that
           scripts/stage_native_path_shim.py derives from the engine's own.

It reads only the upstream directory and this tree, downloads nothing and builds nothing. The output must
not exist.
usage: python -m scripts.prepare_engine_source --upstream DIR --layout windows|macos|linux --output NEW_DIR
"""
import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POCKET = ROOT / "runtime" / "native_pocket"
ENGINE, WINDOWS, LINUX = POCKET / "engine_overrides", POCKET / "windows_resident" / "overrides", POCKET / "engine_overrides_linux"
LAYOUTS = ("windows", "macos", "linux")
HEAD_LINES = 4
DECODER = "mimi_decoder.cpp"
# The block the tree's decoder adds to what the Windows build's engine directory held: from the empty
# line before its comment to the last line of it. Held to that directory's digest, stated in the notice.
BLOCK_BEGINS, BLOCK_ENDS = b"\n        // These tensors escape compute_backend_graph", b"        retain(attention_mask_);\n"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def stated(directory):
    """Each file a notice states: name -> (upstream path, upstream sha256, as-built sha256, its statement).

    The Linux notice says "As kept" where the others say "As built": what its files are is the kept
    source's, and that source is not shown to be what was built."""
    found, name = {}, None
    for line in (directory / "NOTICE").read_text(encoding="utf-8").splitlines():
        if line.startswith("-" * 20):
            break
        if line and not line[0].isspace() and re.fullmatch(r"[a-z_]+\.(cpp|h)", line):
            name = line
            found[name] = []
        elif name is not None and (not line or line[0].isspace()):
            found[name].append(line.strip())
        else:
            name = None
    entries = {}
    for name, lines in found.items():
        text = " ".join(part for part in lines if part)
        path = re.search(r"Upstream: (\S+)", text)
        digests = dict(re.findall(r"(Upstream sha256|As built sha256|As kept sha256): ([0-9a-f]{64})", text))
        if "As kept sha256" in digests:
            digests["As built sha256"] = digests.pop("As kept sha256")
        if not path or set(digests) != {"Upstream sha256", "As built sha256"}:
            raise ValueError(f"{directory / 'NOTICE'} does not state {name} with its upstream file and both digests")
        entries[name] = (path.group(1), digests["Upstream sha256"], digests["As built sha256"], text)
    return entries


def body(path):
    """The tree's file without its head comment: the bytes that were built."""
    return b"\n".join(path.read_bytes().split(b"\n")[HEAD_LINES:])


def replacements(layout, engine=ENGINE, windows=WINDOWS, linux=LINUX):
    """What stands in place of which upstream file: upstream path -> (upstream sha256, bytes, what it is).

    A path whose upstream digest is None is derived from upstream's own file when the upstream is read."""
    if layout not in LAYOUTS:
        raise ValueError("the layout is windows, macos or linux")
    plan = {}
    for name, (path, upstream, built, _) in stated(engine).items():
        data = body(engine / name)
        if sha256(data) != built:
            raise ValueError(f"{engine / name} is not the file its notice states as built")
        plan[path] = (upstream, data, f"engine_overrides/{name}")
    if layout == "linux":
        for name, (path, upstream, built, _) in stated(linux).items():
            data = body(linux / name)
            if sha256(data) != built:
                raise ValueError(f"{linux / name} is not the file its notice states as kept")
            plan[path] = (upstream, data, f"engine_overrides_linux/{name}")
        for path, upstream in path_shim().PINS.items():
            plan[path] = (upstream, None, "derived by scripts/stage_native_path_shim.py")
        return plan
    for name, (path, upstream, built, text) in stated(windows).items():
        data = body(windows / name)
        if sha256(data) != built:
            raise ValueError(f"{windows / name} is not the file its notice states as built")
        if layout == "macos":
            plan[path] = (upstream, data, f"windows_resident/overrides/{name}")
        elif name == DECODER:
            # The digest in the decoder's statement that is neither its upstream's nor its as-built one.
            others = [d for d in re.findall(r"[0-9a-f]{64}", text) if d not in (upstream, built)]
            start, end = data.find(BLOCK_BEGINS), data.find(BLOCK_ENDS)
            if len(others) != 1 or start < 0 or end < start:
                raise ValueError("the decoder's notice or its added block is not as this script knows them")
            without = data[:start] + data[end + len(BLOCK_ENDS):]
            if sha256(without) != others[0]:
                raise ValueError("the decoder without its added block is not the file the notice states for the engine directory")
            plan[path] = (upstream, without, f"windows_resident/overrides/{name} without its added block")
    return plan


def path_shim():
    from scripts import stage_native_path_shim
    return stage_native_path_shim


def prepare(upstream, layout, output, engine=ENGINE, windows=WINDOWS, linux=LINUX):
    upstream, output = Path(upstream), Path(output)
    plan = replacements(layout, engine, windows, linux)
    if output.exists():
        raise ValueError(f"{output} exists; the output must be new")
    for path, (digest, _, _) in sorted(plan.items()):
        source = upstream / path
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"{source} is not a file of the upstream checkout")
        if sha256(source.read_bytes()) != digest:
            raise ValueError(f"{source} is not the upstream file the notice names (sha256 {digest})")
    derived = [path for path, (_, data, _) in plan.items() if data is None]
    if derived:
        # The derivation holds the three files to its own pins and refuses a site it does not know.
        made = path_shim().derive({path: (upstream / path).read_bytes() for path in derived})
        for path in derived:
            plan[path] = (plan[path][0], made[Path(path).name], plan[path][2])
    shutil.copytree(upstream, output, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    for path, (_, data, _) in plan.items():
        (output / path).write_bytes(data)
    return {path: what for path, (_, _, what) in sorted(plan.items())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--layout", required=True, choices=LAYOUTS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        replaced = prepare(args.upstream, args.layout, args.output)
    except ValueError as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    for path, what in replaced.items():
        print(f"{path}  <-  {what}")
    if args.layout == "macos":
        print(f"then, in {args.output}: patch -p1 --forward -i {POCKET / 'metal-library-beside.patch'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
