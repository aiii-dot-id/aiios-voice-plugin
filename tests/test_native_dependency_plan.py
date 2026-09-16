import copy
from types import SimpleNamespace

import pytest

from scripts.native_dependency_plan import removable
from scripts.package_native_runtime import dependency_closure


def test_removal_preserves_unknown_shared_and_notices():
    names = ['gone/code.py', 'kept/code.py', 'shared.py', 'unknown.dll',
             'gone/LICENSE', 'gone/LICENCE', 'gone/NOTICE', 'gone/COPYING',
             'gone/licenses/vendor.txt']
    files = {n: {'bytes': 1, 'sha256': 'test'} for n in names}
    distributions = {'gone': {'files': [n for n in names if n.startswith('gone/')] + ['shared.py']},
                     'keep': {'files': ['kept/code.py', 'shared.py']}}
    assert set(removable(files, distributions, {'keep'})) == {'gone/code.py'}
    changed = copy.deepcopy(distributions)
    changed['gone']['files'].append('not-in-parent')
    with pytest.raises(ValueError, match='outside'):
        removable(files, changed, {'keep'})


def test_closure_keeps_transitive_extras_and_rejects_version_conflicts():
    index = {'root': SimpleNamespace(version='1', requires=['child[voice]>=2']),
             'child': SimpleNamespace(version='2', requires=['audio; extra == "voice"']),
             'audio': SimpleNamespace(version='3', requires=[])}
    assert set(dependency_closure(['root'], index.__getitem__)) == set(index)
    with pytest.raises(ValueError, match='conflicts'):
        dependency_closure(['child>=3'], index.__getitem__)


def test_closure_respects_platform_markers():
    import sys
    index = {'root': SimpleNamespace(version='1', requires=[
        'windows; sys_platform == "win32"', 'other; sys_platform != "win32"']),
        'windows': SimpleNamespace(version='1', requires=[]),
        'other': SimpleNamespace(version='1', requires=[])}
    assert set(dependency_closure(['root'], index.__getitem__)) == {
        'root', 'windows' if sys.platform == 'win32' else 'other'}
