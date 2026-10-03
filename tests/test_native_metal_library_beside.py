"""Pocket's ggml reads its own precompiled Metal kernels, and every check names the same patch.

The worker holds two ggml copies whose kernels differ: NeMo's reads
bin/default.metallib, Pocket's (metal-library-beside.patch) the library named
after its own image. The Apple build refuses an engine source without the
patch, and the checkpoint rebuild refuses a Pocket image without its loader.
Both checks key on text the patch adds; this binds them to it.
"""
from pathlib import Path
import re

from scripts.rebuild_native_checkpoint import METAL_KERNELS

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'runtime/native_pocket/metal-library-beside.patch'


def added_lines():
    return '\n'.join(line[1:].rstrip('\r') for line in PATCH.read_text().splitlines()
                     if line.startswith('+') and not line.startswith('+++'))


def test_the_build_guard_and_the_rebuild_check_key_on_text_the_patch_adds():
    added = added_lines()
    cmake = (ROOT / 'runtime/native_pocket/portable/CMakeLists.txt').read_text()
    guard = re.search(r'if\(NOT loader MATCHES "([^"]+)"\)', cmake).group(1)
    assert guard in added
    _, marker = METAL_KERNELS['lib/libnative_pocket_resident.dylib']
    assert marker.decode() in added
    # The image's kernels are named the way the patched loader derives them.
    assert 'stringByDeletingPathExtension' in added and '@"metallib"' in added
    kernels, _ = METAL_KERNELS['lib/libnative_pocket_resident.dylib']
    assert kernels == 'lib/libnative_pocket_resident.metallib'
    # Nothing in the patched copy defines the class NeMo's ggml also defines.
    assert 'GGMLMetalClass' not in added

