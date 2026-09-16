import json
from pathlib import Path

import pytest

from scripts.prove_native_desktop_linux import process_children
from scripts import prove_native_desktop_windows as windows


def test_linux_counts_children_spawned_on_nonleader_thread(tmp_path):
    for tid, text in ((41, ''), (42, '73 74'), (43, '73')):
        task = tmp_path / '41' / 'task' / str(tid)
        task.mkdir(parents=True)
        (task / 'children').write_text(text)
    assert process_children(41, tmp_path) == [73, 74]


def test_linux_missing_parent_is_not_no_children(tmp_path):
    with pytest.raises(FileNotFoundError):
        process_children(41, tmp_path)


def test_windows_alias_uses_file_identity_and_foreign_file_is_refused(tmp_path, monkeypatch):
    worker = tmp_path / 'aii_voice_worker.exe'
    worker.write_bytes(b'worker')
    alias = tmp_path / 'alternate-name.exe'
    alias.hardlink_to(worker)
    foreign = tmp_path / 'different-worker.exe'
    foreign.write_bytes(worker.read_bytes())
    class ReachedModuleObservation(Exception):
        pass
    def module_open(*args, **kwargs):
        raise ReachedModuleObservation
    monkeypatch.setattr(windows.ctypes, 'WinDLL', module_open, raising=False)
    monkeypatch.setattr(windows.subprocess, 'check_output', lambda *a, **k: json.dumps(
        [{'ProcessId': 73, 'ExecutablePath': str(alias)}]))
    with pytest.raises(ReachedModuleObservation):
        windows.observe_worker(41, tmp_path)
    monkeypatch.setattr(windows.subprocess, 'check_output', lambda *a, **k: json.dumps(
        [{'ProcessId': 73, 'ExecutablePath': str(foreign)}]))
    with pytest.raises(AssertionError):
        windows.observe_worker(41, tmp_path)
