"""Recorded Windows closure falsifiers, not claims of local Windows execution."""

import copy
import json
from pathlib import Path

import pytest

from scripts import package_native_runtime
from scripts.native_dependency_plan import validate

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'deliverables/endpoint-torch-20260910-r1'


def fixture(monkeypatch):
    import packaging.markers

    plan = json.loads((EVIDENCE / 'native-dependency-plan-r1.json').read_text())
    profile = json.loads((EVIDENCE / 'sdk-evidence-r1/endpoint-sdk-runtime-r1/runtime/voice-runtime.json').read_text())
    environment = copy.deepcopy(plan['environment'])
    monkeypatch.setattr(packaging.markers, 'default_environment', lambda: environment)
    monkeypatch.setattr(package_native_runtime, 'default_environment', lambda: environment)
    return profile, plan


def test_exact_recorded_windows_dependency_plan(monkeypatch):
    profile, plan = fixture(monkeypatch)
    removed = validate(profile, plan)
    assert len(removed) == 5214 and sum(r['bytes'] for r in removed.values()) == 243061578
    assert all(n.startswith('deps/') for n in removed)
    assert not any(n.startswith('engine/') for n in removed)


@pytest.mark.parametrize('damage', ['retained-code', 'unowned', 'license', 'root', 'closure', 'version', 'count'])
def test_false_removal_plan_fails(monkeypatch, damage):
    profile, plan = fixture(monkeypatch)
    if damage in {'retained-code', 'unowned', 'license'}:
        if damage == 'retained-code':
            name = next(n for n in profile['files'] if n.endswith('numpy/__init__.py'))
        elif damage == 'unowned':
            name = next(n for n in profile['files'] if n.startswith('engine/'))
        else:
            name = next(n for n in profile['files'] if '/transformers-' in n and n.endswith('/LICENSE'))
        plan['removed_files'][name] = profile['files'][name]
    elif damage == 'root':
        plan['requirements'].remove('kaldi-native-fbank')
    elif damage == 'closure':
        del plan['resolved']['kaldi-native-fbank']
    elif damage == 'version':
        plan['distributions']['numpy']['version'] = '0.1'
    else:
        plan['removed_file_count'] -= 1
    with pytest.raises(ValueError):
        validate(profile, plan)
