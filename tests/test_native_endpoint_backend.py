import ctypes
import hashlib
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest

from runtime.native_endpoint import backend, profile
from tests.conftest import skip_unless_shipped


def binding(tmp_path):
    files = {}
    for n, raw in {
        "lib/native-endpoint/aii_native_endpoint.dll": b"endpoint",
        "lib/native-endpoint/onnxruntime.dll": b"ort",
        "resources/endpoint/windows-coefficients.f32": np.ones(16480, dtype="<f4").tobytes(),
    }.items():
        p = tmp_path / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        files[n] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    return {"platform": "windows", "arch": "amd64", "backend": "windows-pocket", "stt_backend": "native-directml",
            "tts_backend": "native-pocket-vulkan", "endpoint_backend": "native-aten-cpu", "files": files,
            "native_endpoint": {"library": "lib/native-endpoint/aii_native_endpoint.dll",
                                "coefficients": "resources/endpoint/windows-coefficients.f32"}}


def test_explicit_bound_selection(tmp_path):
    assert profile.selection(None) is None
    assert profile.selection(tmp_path, {}) is None
    p = binding(tmp_path)
    assert profile.selection(tmp_path, p)["library"] == tmp_path / p["native_endpoint"]["library"]


@pytest.mark.parametrize("damage", ["no-backend", "null", "extra", "wrong-backend", "mac", "conflict", "unlisted", "changed", "outside", "coefficient-size"])
def test_bad_selection_refused_before_loading(tmp_path, damage):
    p = binding(tmp_path)
    if damage == "no-backend": del p["endpoint_backend"]
    elif damage == "null": p["native_endpoint"] = None
    elif damage == "extra": p["native_endpoint"]["automatic"] = True
    elif damage == "wrong-backend": p["endpoint_backend"] = "automatic"
    elif damage == "mac": p["platform"] = "darwin"
    elif damage == "conflict": p["endpoint_frontend"] = {}
    elif damage == "unlisted": (tmp_path / "lib/native-endpoint/extra.dll").write_bytes(b"x")
    elif damage == "changed": (tmp_path / p["native_endpoint"]["library"]).write_bytes(b"changed!")
    elif damage == "outside": p["native_endpoint"]["library"] = "/outside.dll"
    elif damage == "coefficient-size": p["files"][p["native_endpoint"]["coefficients"]]["bytes"] = 1
    with pytest.raises(ValueError): profile.selection(tmp_path, p)


class Function:
    def __init__(self, fn): self.fn = fn
    def __call__(self, *args): return self.fn(*args)


class Library:
    def __init__(self):
        self.started, self.release = threading.Event(), threading.Event()
        self.block = False
        self.ids, self.cancelled, self.destroyed = [], [], []
        self.value, self.code = .625, 0
        for name, fn in {"create": lambda *a: 123, "score": self.score,
                         "cancel_through": self.cancel, "destroy": self.destroyed.append,
                         "phase": lambda _: int(self.started.is_set() and not self.release.is_set())}.items():
            setattr(self, "aii_endpoint_" + name, Function(fn))

    def cancel(self, handle, query):
        assert handle == 123
        self.cancelled.append(query)
        return 0

    def score(self, handle, query, pcm, count, probability, features, capacity, error, error_capacity):
        self.ids.append(query)
        self.started.set()
        if self.block: assert self.release.wait(3)
        ctypes.cast(probability, ctypes.POINTER(ctypes.c_double))[0] = self.value
        if features: np.ctypeslib.as_array(features, shape=(capacity,))[:] = 1
        return self.code


def engine(tmp_path, monkeypatch):
    cfg = profile.selection(tmp_path, binding(tmp_path))
    model = tmp_path / "model/smart-turn-v3.2-cpu.onnx"
    model.parent.mkdir()
    model.write_bytes(b"bound model")
    assets = SimpleNamespace(snapshot=lambda role: model.parent, manifest_sha=lambda role: "manifest",
        groups={"endpoint": {"files": {model.name: {"sha256": hashlib.sha256(model.read_bytes()).hexdigest()}}}})
    lib = Library()
    monkeypatch.setattr(backend, "open_library", lambda path: lib)
    monkeypatch.setenv("ORT_DISABLE_TELEMETRY", "1")
    return backend.NativeEndpoint(cfg, assets), lib


def test_probability_features_and_fresh_query_after_cancel(tmp_path, monkeypatch):
    e, lib = engine(tmp_path, monkeypatch)
    p, features = e.score(np.zeros(512), features=True)
    assert p == .625 and features.shape == (1, 80, 800) and (features == 1).all()
    e.cancel()
    assert e.probability(np.ones(512)) == .625 and lib.ids == [1, 2]
    e.close(); e.close()
    assert lib.destroyed == [123]
    with pytest.raises(RuntimeError, match="closed"): e.probability(np.ones(512))


@pytest.mark.parametrize("bad", [[], [[0]], [float("nan")], [float("inf")], np.zeros(960001)])
def test_invalid_audio_never_calls_native(tmp_path, monkeypatch, bad):
    e, lib = engine(tmp_path, monkeypatch)
    with pytest.raises(ValueError): e.probability(bad)
    assert not lib.ids
    e.close()


@pytest.mark.parametrize("value,code", [(float("nan"), 0), (-.1, 0), (1.1, 0), (.5, 4)])
def test_faults_are_not_completions(tmp_path, monkeypatch, value, code):
    e, lib = engine(tmp_path, monkeypatch)
    lib.value, lib.code = value, code
    with pytest.raises(RuntimeError): e.probability(np.ones(512))
    with pytest.raises(RuntimeError, match="faulted"): e.probability(np.ones(512))
    e.close()


def test_cancel_does_not_wait_and_close_keeps_live_handle(tmp_path, monkeypatch):
    e, lib = engine(tmp_path, monkeypatch)
    lib.block = True
    with ThreadPoolExecutor(1) as executor:
        result = executor.submit(e.probability, np.ones(512))
        assert lib.started.wait(1)
        begin = time.monotonic()
        e.cancel()
        assert time.monotonic() - begin < .05 and not result.done()
        with pytest.raises(RuntimeError, match="inference owner"): e.probability(np.ones(512))
        with pytest.raises(TimeoutError, match="not retired"): e.close(timeout=.001)
        assert not lib.destroyed
        lib.release.set()
        with pytest.raises(CancelledError): result.result(timeout=1)
    e.close()
    assert lib.destroyed == [123]


def test_cancelled_success_is_not_published_and_recovery_works(tmp_path, monkeypatch):
    e, lib = engine(tmp_path, monkeypatch)
    lib.block = True
    with ThreadPoolExecutor(1) as executor:
        pending = executor.submit(e.probability, np.ones(512))
        assert lib.started.wait(1)
        e.cancel()
        lib.release.set()  # The fake native call returns success despite cancellation.
        with pytest.raises(CancelledError):
            pending.result(timeout=1)
    lib.block = False
    assert e.probability(np.ones(512)) == .625 and lib.ids == [1, 2]
    e.close()


def test_native_import_contract_cannot_hide_torch():
    skip_unless_shipped("scripts.prove_windows_packaged_runtime")
    from scripts.prove_windows_packaged_runtime import verify_import_versions

    versions = {"numpy": "bound", "onnxruntime-directml": "bound"}
    p = {"endpoint_backend": "native-aten-cpu", "distributions": {"tts": versions}}
    verify_import_versions(p, "tts", {"versions": versions, "modules": {}})
    for name in ("torch", "torch.nn", "transformers"):
        with pytest.raises(ValueError, match="removed framework"):
            verify_import_versions(p, "tts", {"versions": versions, "modules": {name: "loaded"}})
