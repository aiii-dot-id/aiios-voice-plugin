"""The engine files the speaking libraries are built with in place of upstream's are in the tree, and say what they are."""
import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OVERRIDES = ROOT / 'runtime' / 'native_pocket' / 'engine_overrides'
WINDOWS = ROOT / 'runtime' / 'native_pocket' / 'windows_resident' / 'overrides'
ENGINE_REVISION = '3174e6b26f11a0e39b4f150961dce98f43ba860d'
HEAD_LINES = 4
# Each file here, the engine's path it stands for, and whose it is.
FILES = {
    'vec.h': ('external/ggml/src/ggml-cpu/vec.h', 'ggml'),
    'transformer_blocks.h': ('include/engine/framework/modules/attention/transformer_blocks.h', 'engine'),
    'transformer_blocks.cpp': ('src/framework/modules/attention/transformer_blocks.cpp', 'engine'),
    'flow_lm.cpp': ('src/models/pocket_tts/flow_lm.cpp', 'engine'),
    'graph_common.h': ('src/models/pocket_tts/graph_common.h', 'engine'),
}
OWNER = {'engine': ('Copyright 2026 ShugoAI LLC', 'Apache License, Version 2.0'),
         'ggml': ('Copyright (c) 2023-2026 The ggml authors', 'MIT License')}
PERMISSION = 'Permission is hereby granted, free of charge, to any person obtaining a copy'
CONDITION = 'The above copyright notice and this permission notice shall be included in all'
# The change itself, as each file holds it and as its statement names it: a file whose digest and statement
# agree with each other and with nothing that was built must not pass.
SWITCH = {'graph_common.h': ('GeluApproximation::Tanh', 'GeluApproximation::Tanh'),
          'transformer_blocks.cpp': ('config_.gelu_approximation', 'gelu_approximation'),
          'transformer_blocks.h': ('GeluApproximation gelu_approximation', 'gelu_approximation'),
          'vec.h': ('GGML_GELU_FORCE_F32', 'GGML_GELU_FORCE_F32'),
          'flow_lm.cpp': ('graph_common::pocket_transformer_config', 'pocket_transformer_config')}


def entries(notice):
    """The notice's entries: a file name alone at the start of a line, then its indented statement."""
    found, name = {}, None
    for line in notice.splitlines():
        if line.startswith('-' * 20): break
        if line and not line[0].isspace() and re.fullmatch(r'[a-z_]+\.(cpp|h)', line):
            name = line; found[name] = []
        elif name is not None and (not line or line[0].isspace()):
            found[name].append(line.strip())
        else:
            name = None
    return {name: ' '.join(part for part in lines if part) for name, lines in found.items()}


def check(directory=OVERRIDES, windows=WINDOWS):
    notice = (directory / 'NOTICE').read_text(encoding='utf-8')
    stated = entries(notice)
    present = sorted(path.name for path in directory.iterdir() if path.name != 'NOTICE')
    if sorted(stated) != present or present != sorted(FILES):
        raise ValueError('the notice lists %s, the directory holds %s and the files are %s' % (
            sorted(stated), present, sorted(FILES)))
    if ENGINE_REVISION not in notice or OWNER['engine'][0] not in notice or 'Apache License, Version' not in notice:
        raise ValueError("the notice does not state the engine's revision, its copyright and its licence")
    terms = notice.split('-' * 20, 1)[1] if '-' * 20 in notice else ''
    if PERMISSION not in terms or CONDITION not in terms or OWNER['ggml'][0] not in terms:
        raise ValueError("ggml's terms are not reproduced whole")
    for name, statement in stated.items():
        path, owner = FILES[name]
        raw = (directory / name).read_bytes()
        lines = raw.split(b'\n')
        head, body = b'\n'.join(lines[:HEAD_LINES]).decode(), b'\n'.join(lines[HEAD_LINES:])
        copyright_line, licence = OWNER[owner]
        if not (all(line.startswith('//') for line in head.splitlines()) and 'Modified from' in head and path in head
                and ENGINE_REVISION in head and copyright_line in head and licence in head and 'NOTICE' in head):
            raise ValueError(name + ' does not say that it was changed, from what, and under which terms')
        fields = dict(re.findall(r'(Upstream sha256|As built sha256): ([0-9a-f]{64})', statement))
        if set(fields) != {'Upstream sha256', 'As built sha256'} or 'Upstream: ' + path not in statement:
            raise ValueError(name + ' is not stated with its upstream file and both digests')
        if hashlib.sha256(body).hexdigest() != fields['As built sha256']:
            raise ValueError(name + ' is not the file the libraries were built from with a head comment added')
        if fields['Upstream sha256'] == fields['As built sha256'] or 'Changed: ' not in statement:
            raise ValueError(name + ' does not state what was changed')
        if owner == 'ggml' and 'MIT License' not in statement:
            raise ValueError(name + " is ggml's and its statement does not say under which licence it stays")
        if SWITCH[name][0] not in body.decode('utf-8') or SWITCH[name][1] not in statement:
            raise ValueError(name + ' does not hold the change its statement describes')
    # The two files the Windows recipe compiles are stated beside it, and each notice names the other:
    # neither directory alone is everything that was changed.
    other = (windows / 'NOTICE').read_text(encoding='utf-8')
    if 'windows_resident/overrides' not in notice or 'engine_overrides' not in other:
        raise ValueError('the two notices do not name each other')
    if 'pocket_transformer_config' not in (windows / 'mimi_decoder.cpp').read_text(encoding='utf-8') or \
            'pocket_transformer_config' not in other:
        raise ValueError("the decoder's use of the configuration here is not stated beside the decoder")


def test_every_replaced_engine_file_is_stated_and_is_what_was_built():
    check()


@pytest.fixture
def copy(tmp_path):
    for source, name in ((OVERRIDES, 'engine'), (WINDOWS, 'windows')):
        (tmp_path / name).mkdir()
        for path in source.iterdir():
            (tmp_path / name / path.name).write_bytes(path.read_bytes())
    return tmp_path


def rewrite(path, old, new):
    data = path.read_bytes()
    assert data.count(old.encode()) >= 1, old
    path.write_bytes(data.replace(old.encode(), new.encode(), 1))


@pytest.mark.parametrize('fault', ['file-not-listed', 'listed-file-absent', 'one-line-of-code-changed', 'head-comment-gone',
    'head-comment-names-another-owner', 'as-built-digest-of-another-file', 'change-not-stated', 'revision-gone',
    'ggml-terms-cut', 'ggml-licence-not-in-its-statement', 'the-switch-is-not-in-the-file', 'notices-do-not-name-each-other',
    'decoder-use-not-stated'])
def test_a_statement_that_is_not_true_of_the_files_is_refused(copy, fault):
    with pytest.raises(ValueError):
        check(*plant(copy, fault))


def plant(copy, fault):
    engine, windows = copy / 'engine', copy / 'windows'
    notice = engine / 'NOTICE'
    if fault == 'file-not-listed': (engine / 'session.cpp').write_text('// another replaced file\n')
    if fault == 'listed-file-absent': (engine / 'flow_lm.cpp').unlink()
    if fault == 'one-line-of-code-changed': rewrite(engine / 'graph_common.h', 'GeluApproximation::Tanh;', 'GeluApproximation::Tanh ;')
    if fault == 'head-comment-gone':
        raw = (engine / 'transformer_blocks.h').read_bytes().split(b'\n')
        (engine / 'transformer_blocks.h').write_bytes(b'\n'.join(raw[HEAD_LINES:]))
    if fault == 'head-comment-names-another-owner': rewrite(engine / 'vec.h', 'The ggml authors', 'ShugoAI LLC')
    if fault == 'as-built-digest-of-another-file':
        rewrite(notice, '68edfc940d9bce261d5f4c57cb601131f65fb627180978a46ed318ad8a610b07',
                '40f55d48971f72fce61be995af4432cc35a201c7c5b2fdc3e15fd726da6a7080')
    if fault == 'change-not-stated': rewrite(notice, 'Changed: one line added.', 'One line added.')
    if fault == 'revision-gone':
        rewrite(notice, ENGINE_REVISION, 'the pinned revision')
    if fault == 'ggml-terms-cut': rewrite(notice, CONDITION, 'The notices may be left out of')
    if fault == 'ggml-licence-not-in-its-statement': rewrite(notice, 'distributed under the MIT License reproduced', 'distributed under the terms reproduced')
    if fault == 'the-switch-is-not-in-the-file':
        # The file and its stated digest agree with each other and with nothing that was built.
        rewrite(engine / 'transformer_blocks.cpp', 'config_.gelu_approximation', 'GeluApproximation::Tanh')
        rewrite(engine / 'transformer_blocks.cpp', 'config_.gelu_approximation', 'GeluApproximation::Tanh')
        raw = (engine / 'transformer_blocks.cpp').read_bytes().split(b'\n')
        rewrite(notice, '68edfc940d9bce261d5f4c57cb601131f65fb627180978a46ed318ad8a610b07',
                hashlib.sha256(b'\n'.join(raw[HEAD_LINES:])).hexdigest())
    if fault == 'notices-do-not-name-each-other':
        (windows / 'NOTICE').write_text((windows / 'NOTICE').read_text().replace('engine_overrides', 'another_directory'))
    if fault == 'decoder-use-not-stated': rewrite(windows / 'NOTICE', 'graph_common::pocket_transformer_config', 'a function of graph_common.h')
    return engine, windows


# THE LINUX LIBRARY'S SOURCE. Its three files are held as the other directories' are, and one thing more:
# what is not known about that library stays said.
LINUX = ROOT / 'runtime' / 'native_pocket' / 'engine_overrides_linux'
# Each file, and what of its change the file itself must hold.
LINUX_FILES = {'acoustic_model.cpp': 'runtime_cache_.prompt_capacity == prompt_capacity',
               'mimi_decoder.cpp': '#include "streaming_tail.h"',
               'session.cpp': 'canonical GPU prompt capacity overflow'}


def check_linux(directory=LINUX):
    notice = (directory / 'NOTICE').read_text(encoding='utf-8')
    stated = entries(notice)
    present = sorted(path.name for path in directory.iterdir() if path.name != 'NOTICE')
    if sorted(stated) != present or present != sorted(LINUX_FILES):
        raise ValueError('the notice lists %s, the directory holds %s and the files are %s' % (
            sorted(stated), present, sorted(LINUX_FILES)))
    if ENGINE_REVISION not in notice or OWNER['engine'][0] not in notice or 'Apache License, Version' not in notice:
        raise ValueError("the notice does not state the engine's revision, its copyright and its licence")
    if 'is not recorded' not in notice or 'is not established' not in notice:
        raise ValueError('the notice no longer says what is not known about how the Linux library was built')
    for name, statement in stated.items():
        path = 'src/models/pocket_tts/' + name
        lines = (directory / name).read_bytes().split(b'\n')
        head, body = b'\n'.join(lines[:HEAD_LINES]).decode(), b'\n'.join(lines[HEAD_LINES:])
        if not (all(line.startswith('//') for line in head.splitlines()) and 'Modified from' in head and path in head
                and ENGINE_REVISION in head and OWNER['engine'][0] in head and OWNER['engine'][1] in head and 'NOTICE' in head):
            raise ValueError(name + ' does not say that it was changed, from what, and under which terms')
        fields = dict(re.findall(r'(Upstream sha256|As kept sha256): ([0-9a-f]{64})', statement))
        if set(fields) != {'Upstream sha256', 'As kept sha256'} or 'Upstream: ' + path not in statement:
            raise ValueError(name + ' is not stated with its upstream file and both digests')
        if hashlib.sha256(body).hexdigest() != fields['As kept sha256']:
            raise ValueError(name + ' is not the file of the kept source with a head comment added')
        if fields['Upstream sha256'] == fields['As kept sha256'] or 'Changed: ' not in statement:
            raise ValueError(name + ' does not state what was changed')
        if LINUX_FILES[name] not in body.decode('utf-8'):
            raise ValueError(name + ' does not hold the change its statement describes')
    # The patch that was said to be in every library is in this source by one of its two changes only.
    if 'capacity-history.patch' not in stated['acoustic_model.cpp'] or 'is NOT in this file' not in stated['session.cpp']:
        raise ValueError('which change of capacity-history.patch this source holds, and which it does not, is not stated')


def test_the_linux_sources_three_files_are_stated_with_what_is_not_known():
    check_linux()


@pytest.mark.parametrize('fault', ['one-line-of-code-changed', 'what-is-not-recorded-is-dropped', 'what-is-not-established-is-dropped',
    'kept-digest-of-another-file', 'head-comment-gone', 'the-patch-is-not-mentioned', 'the-absent-change-is-not-stated', 'file-not-listed'])
def test_a_linux_statement_that_is_not_true_of_the_files_is_refused(tmp_path, fault):
    for path in LINUX.iterdir():
        (tmp_path / path.name).write_bytes(path.read_bytes())
    notice = tmp_path / 'NOTICE'
    if fault == 'one-line-of-code-changed': rewrite(tmp_path / 'session.cpp', 'prompt_steps * 2', 'prompt_steps * 3')
    if fault == 'what-is-not-recorded-is-dropped': rewrite(notice, 'was built is not recorded', 'was built is recorded elsewhere')
    if fault == 'what-is-not-established-is-dropped': rewrite(notice, 'exactly these bytes is not established', 'exactly these bytes is plain')
    if fault == 'kept-digest-of-another-file':
        rewrite(notice, '93489ceb29709968a1072b80c94ff994368f384f807045c0fa57cdf59c0b75d2',
                'c678bba50eb5711e0c37512c1e9ff3bdfe92a2ed784d7971dff2e6baeeab0f4d')
    if fault == 'head-comment-gone':
        raw = (tmp_path / 'mimi_decoder.cpp').read_bytes().split(b'\n')
        (tmp_path / 'mimi_decoder.cpp').write_bytes(b'\n'.join(raw[HEAD_LINES:]))
    if fault == 'the-patch-is-not-mentioned': rewrite(notice, '../capacity-history.patch makes', 'a patch makes')
    if fault == 'the-absent-change-is-not-stated': rewrite(notice, 'is NOT in this file', 'is elsewhere')
    if fault == 'file-not-listed': (tmp_path / 'flow_lm.cpp').write_text('// another\n')
    with pytest.raises(ValueError):
        check_linux(tmp_path)
