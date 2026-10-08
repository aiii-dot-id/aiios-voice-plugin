"""Every Android override states where it comes from, and the copy of ggml keeps ggml's terms."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OVERRIDES = ROOT / 'runtime' / 'native_pocket' / 'android' / 'overrides'
DERIVED = 'ggml-vulkan.cpp'
# Any identifier this project added to the copy carries its name.
OURS = re.compile(r'[A-Za-z0-9_]*[Aa][Ii][Ii]_[A-Za-z0-9_]+')
PERMISSION = 'Permission is hereby granted, free of charge, to any person obtaining a copy'
CONDITION = 'The above copyright notice and this permission notice shall be included in all'
COPYRIGHT = re.compile(r'Copyright \(c\) [0-9]{4}(-[0-9]{4})? The ggml authors')


def entries(notice):
    """The notice's entries: a file name alone at the start of a line, then its indented statement."""
    found, name = {}, None
    for line in notice.splitlines():
        if line.startswith('-' * 20): break
        if line and not line[0].isspace() and '/' not in line and ' ' not in line and '.' in line:
            name = line; found[name] = []
        elif name is not None and (not line or line[0].isspace()):
            found[name].append(line.strip())
        else:
            name = None
    return {name: ' '.join(part for part in lines if part) for name, lines in found.items()}


def check(directory=OVERRIDES):
    notice = (directory / 'NOTICE').read_text(encoding='utf-8')
    stated = entries(notice)
    present = sorted(path.name for path in directory.iterdir() if path.name != 'NOTICE')
    if sorted(stated) != present:
        raise ValueError('the notice lists %s and the directory holds %s' % (sorted(stated), present))
    for name, statement in stated.items():
        if name == DERIVED: continue
        if 'Written for this project' not in statement or 'Apache-2.0' not in statement:
            raise ValueError(name + ' does not state its origin and licence')
    derived = stated[DERIVED]
    if 'MIT License' not in derived or not re.search(r'Upstream revision: (NOT RECORDED|[0-9a-f]{40})', derived):
        raise ValueError('the copy of ggml does not state its licence and its upstream revision')
    # A base that is stated is stated with the bytes it was compared against.
    if 'Upstream revision: NOT RECORDED' not in derived and not re.search(r'sha256 [0-9a-f]{64}', derived):
        raise ValueError('the copy of ggml names a base and not the bytes it was compared against')
    terms = notice.split('-' * 20, 1)[1] if '-' * 20 in notice else ''
    if PERMISSION not in terms or CONDITION not in terms or not COPYRIGHT.search(terms):
        raise ValueError("ggml's terms are not reproduced whole")
    source = (directory / DERIVED).read_text(encoding='utf-8')
    head = source[:source.index('#include')]
    if not COPYRIGHT.search(head) or 'MIT License' not in head or 'NOTICE' not in head:
        raise ValueError('the copy of ggml does not carry its copyright line and point at the notice')
    unstated = sorted(name for name in set(OURS.findall(source)) if name not in derived)
    if unstated:
        raise ValueError('changes to the copy of ggml that the notice does not state: ' + ', '.join(unstated))


def test_every_override_states_its_origin():
    check()


@pytest.fixture
def copy(tmp_path):
    for path in OVERRIDES.iterdir():
        (tmp_path / path.name).write_bytes(path.read_bytes())
    return tmp_path


def rewrite(path, old, new, count=1):
    text = path.read_text(encoding='utf-8')
    assert text.count(old) >= count, old
    path.write_text(text.replace(old, new), encoding='utf-8')


@pytest.mark.parametrize('fault', ['file-not-listed', 'listed-file-absent', 'origin-not-stated',
    'revision-not-stated', 'base-bytes-not-stated', 'terms-cut', 'copyright-line-gone', 'head-comment-gone', 'unstated-change'])
def test_an_incomplete_statement_is_refused(copy, fault):
    notice, source = copy / 'NOTICE', copy / DERIVED
    if fault == 'file-not-listed': (copy / 'another.comp').write_text('void main() {}\n')
    if fault == 'listed-file-absent': (copy / 'scalar_tile_warp.h').unlink()
    if fault == 'origin-not-stated': rewrite(notice, 'Written for this project. Apache-2.0', 'A header. Apache-2.0')
    if fault == 'revision-not-stated': rewrite(notice, 'Upstream revision: 3174e6b26f11a0e39b4f150961dce98f43ba860d', 'Upstream revision: recent')
    if fault == 'base-bytes-not-stated': rewrite(notice, 'sha256 bf3ca8dcb06f29e866a430e1d546fd6db200c0291a214508e3d7e459cf1daedf', 'its bytes')
    if fault == 'terms-cut': rewrite(notice, CONDITION, 'The notices may be left out of')
    if fault == 'copyright-line-gone': rewrite(notice, 'Copyright (c) 2023-2026 The ggml authors\n\nPermission', 'Permission')
    if fault == 'head-comment-gone':
        text = source.read_text(encoding='utf-8')
        source.write_text(text[text.index('#include'):], encoding='utf-8')
    if fault == 'unstated-change': rewrite(source, '#include "scalar_tile_warp.h"', '#include "scalar_tile_warp.h"\nstatic int aii_new_path = 0;')
    with pytest.raises(ValueError):
        check(copy)
