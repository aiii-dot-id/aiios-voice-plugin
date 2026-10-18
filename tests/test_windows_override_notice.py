"""The Windows build's two replaced engine files are in the tree, say what they are, and are what was built."""
import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'runtime' / 'native_pocket' / 'windows_resident'
OVERRIDES = RECIPE / 'overrides'
ENGINE_REVISION = '3174e6b26f11a0e39b4f150961dce98f43ba860d'
HEAD_LINES = 4
# The recipe's variable for each file it compiles in place of the engine's own.
COMPILED_AS = {'acoustic_model.cpp': 'ACOUSTIC_OVERRIDE', 'mimi_decoder.cpp': 'MIMI_OVERRIDE'}


def entries(notice):
    """The notice's entries: a file name alone at the start of a line, then its indented statement."""
    found, name = {}, None
    for line in notice.splitlines():
        if line and not line[0].isspace() and re.fullmatch(r'[a-z_]+\.(cpp|h)', line):
            name = line; found[name] = []
        elif name is not None and (not line or line[0].isspace()):
            found[name].append(line.strip())
        else:
            name = None
    return {name: ' '.join(part for part in lines if part) for name, lines in found.items()}


def check(directory=OVERRIDES, recipe=RECIPE / 'CMakeLists.txt'):
    notice = (directory / 'NOTICE').read_text(encoding='utf-8')
    stated = entries(notice)
    present = sorted(path.name for path in directory.iterdir() if path.name != 'NOTICE')
    if sorted(stated) != present or present != sorted(COMPILED_AS):
        raise ValueError('the notice lists %s, the directory holds %s and the recipe compiles %s' % (
            sorted(stated), present, sorted(COMPILED_AS)))
    if ENGINE_REVISION not in notice or 'Apache License, Version' not in notice or 'Copyright 2026 ShugoAI LLC' not in notice:
        raise ValueError("the notice does not state the engine's revision, its copyright and its licence")
    build = recipe.read_text(encoding='utf-8')
    for name, statement in stated.items():
        raw = (directory / name).read_bytes()
        lines = raw.split(b'\n')
        head, body = b'\n'.join(lines[:HEAD_LINES]).decode(), b'\n'.join(lines[HEAD_LINES:])
        if not all(line.startswith('//') for line in head.splitlines()) or lines[HEAD_LINES].startswith(b'//'):
            raise ValueError(name + ' does not begin with its four-line notice and nothing more')
        if not ('Modified from audio.cpp' in head and 'src/models/pocket_tts/' + name in head and ENGINE_REVISION in head
                and 'Copyright 2026 ShugoAI LLC' in head and 'Apache License' in head and 'NOTICE' in head):
            raise ValueError(name + ' does not say that it was changed, from what, and under which terms')
        fields = dict(re.findall(r'(Upstream sha256|As built sha256): ([0-9a-f]{64})', statement))
        if set(fields) != {'Upstream sha256', 'As built sha256'} or 'Upstream: src/models/pocket_tts/' + name not in statement:
            raise ValueError(name + ' is not stated with its upstream file and both digests')
        if hashlib.sha256(body).hexdigest() != fields['As built sha256']:
            raise ValueError(name + ' is not the file the library was built from with a head comment added')
        if fields['Upstream sha256'] == fields['As built sha256'] or 'Changed: ' not in statement:
            raise ValueError(name + ' does not state what was changed')
        if ('"${%s}"' % COMPILED_AS[name]) not in build:
            raise ValueError('the recipe no longer compiles ' + name + ' as ' + COMPILED_AS[name])


def test_the_two_replaced_files_are_stated_and_are_what_was_built():
    check()


@pytest.fixture
def copy(tmp_path):
    for path in OVERRIDES.iterdir():
        (tmp_path / path.name).write_bytes(path.read_bytes())
    return tmp_path


def rewrite(path, old, new):
    text = path.read_text(encoding='utf-8')
    assert text.count(old) >= 1, old
    path.write_text(text.replace(old, new, 1), encoding='utf-8')


@pytest.mark.parametrize('fault', ['file-not-listed', 'listed-file-absent', 'one-line-of-code-changed', 'head-comment-gone',
    'head-comment-says-nothing', 'as-built-digest-of-another-file', 'change-not-stated', 'revision-gone', 'recipe-compiles-another'])
def test_a_statement_that_is_not_true_of_the_files_is_refused(copy, tmp_path, fault):
    notice, source = copy / 'NOTICE', copy / 'acoustic_model.cpp'
    recipe = RECIPE / 'CMakeLists.txt'
    if fault == 'file-not-listed': (copy / 'flow.cpp').write_text('// another override\n')
    if fault == 'listed-file-absent': (copy / 'mimi_decoder.cpp').unlink()
    if fault == 'one-line-of-code-changed': rewrite(source, 'state.done = true;', 'state.done = false;')
    if fault == 'head-comment-gone':
        raw = source.read_bytes().split(b'\n')
        source.write_bytes(b'\n'.join(raw[HEAD_LINES:]))
    if fault == 'head-comment-says-nothing': rewrite(source, 'Copyright 2026 ShugoAI LLC', 'Copyright the authors')
    if fault == 'as-built-digest-of-another-file':
        rewrite(notice, '81a40bcb089e48dac1dc351f3ca189da282e6907270e390053a9af08a84481eb',
                '29d6fd43febf041ea8e6c202e29d5920680e05b9125c658af28c5960a3347fc5')
    if fault == 'change-not-stated': rewrite(notice, 'Changed: one line.', 'One line.')
    if fault == 'revision-gone': rewrite(notice, ENGINE_REVISION, 'the pinned revision')
    if fault == 'recipe-compiles-another':
        recipe = tmp_path / 'recipe.txt'
        recipe.write_text((RECIPE / 'CMakeLists.txt').read_text().replace('"${ACOUSTIC_OVERRIDE}"', '"${ACOUSTIC_SOURCE}"'))
    with pytest.raises(ValueError):
        check(copy, recipe)
