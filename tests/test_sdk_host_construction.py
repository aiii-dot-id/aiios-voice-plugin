"""Diagnostic ownership under real pipe/spawn failures; no engine or models."""
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from runtime.plugin_engine import worker
from scripts import prove_plugin_sdk_engine as sdk


@pytest.fixture
def owned_probe(tmp_path, monkeypatch):
    original_pipe, original_fdopen = os.pipe, os.fdopen
    original_popen, original_start = subprocess.Popen, threading.Thread.start
    original_open = Path.open
    state = SimpleNamespace(pipes=[], streams=[], logs=[], children=[], readers=[], stage=None, pipe_count=0, open_count=0, start_count=0)
    monkeypatch.setattr(sdk, "describe_carrier", lambda path: {})
    monkeypatch.setattr(worker, "native_candidate_options", lambda args: {})

    def pipe():
        state.pipe_count += 1
        if state.stage == "second_pipe" and state.pipe_count == 2:
            raise OSError("injected second pipe failure")
        pair = original_pipe()
        if state.pipe_count <= 2:
            state.pipes.extend(pair)
        return pair

    def fdopen(*args, **kwargs):
        state.open_count += 1
        if state.stage == "second_fdopen" and state.open_count == 2:
            raise OSError("injected second fdopen failure")
        stream = original_fdopen(*args, **kwargs)
        state.streams.append(stream)
        return stream

    def path_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path.name == "worker.stderr.log":
            state.logs.append(stream)
        return stream

    def popen(command, **kwargs):
        if state.stage == "spawn":
            raise OSError("injected spawn failure")
        # A real owned process keeps the inherited descriptors until EOF.
        # No carrier, native worker, speech model or device is loaded.
        child = original_popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"], **kwargs)
        state.children.append(child)
        return child

    def start(thread):
        state.start_count += 1
        state.readers.append(thread)
        if state.stage == "second_reader" and state.start_count == 2:
            raise RuntimeError("injected second reader failure")
        return original_start(thread)

    monkeypatch.setattr(os, "pipe", pipe)
    monkeypatch.setattr(os, "fdopen", fdopen)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(threading.Thread, "start", start)
    monkeypatch.setattr(Path, "open", path_open)
    state.args = SimpleNamespace(output=tmp_path, fixture=True, carrier=tmp_path / "unused-carrier")
    state.host = sdk.SDKHost.__new__(sdk.SDKHost)
    yield state
    # Test custody also cleans the deliberately broken baseline, after the
    # assertion. Never scan for or terminate unrelated processes.
    for child in state.children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()
    for stream in state.streams + state.logs:
        stream.close()
    for fd in state.pipes:
        try:
            os.close(fd)
        except OSError:
            pass
    for thread in state.readers:
        if thread.ident is not None:
            thread.join(timeout=3)
            assert not thread.is_alive(), "test-owned reader outlived cleanup"


def assert_retired(state):
    leaked = []
    for fd in state.pipes:
        try:
            os.fstat(fd)
        except OSError:
            continue
        leaked.append(fd)
    assert not leaked, f"constructor failure leaked pipe descriptors: {leaked}"
    assert all(stream.closed for stream in state.streams + state.logs), "constructor failure left owned files open"
    assert all(child.poll() is not None for child in state.children), "constructor failure orphaned its child"
    assert all(child.stdin.closed and child.stdout.closed for child in state.children), "constructor failure left child pipes open"
    assert all(not thread.is_alive() for thread in state.readers), "constructor failure left reader alive"


@pytest.mark.parametrize("stage", ["second_pipe", "second_fdopen", "spawn", "second_reader"])
def test_sdk_host_failed_construction_retires_what_it_owns(owned_probe, stage):
    state = owned_probe
    state.stage = stage
    with pytest.raises((OSError, RuntimeError), match="injected"):
        sdk.SDKHost.__init__(state.host, state.args)
    assert_retired(state)


def test_readiness_failure_is_caller_owned_and_close_releases_every_pipe(owned_probe):
    state = owned_probe
    sdk.SDKHost.__init__(state.host, state.args)
    try:
        with pytest.raises(TimeoutError, match="SDK readiness"):
            state.host.readiness(timeout=0.01)
        assert state.host.process.poll() is None, "readiness observation unexpectedly took lifecycle ownership"
    finally:
        assert state.host.close() == 0
    assert_retired(state)
