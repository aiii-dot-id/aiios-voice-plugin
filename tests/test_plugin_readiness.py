import json
from types import SimpleNamespace

import numpy as np
import pytest

from runtime.plugin_engine.readiness import warm_models
from scripts.prove_plugin_sdk_engine import SDKHost


def models_fixture():
    calls = []

    class Stream:
        def push_audio(self, pcm):
            calls.append(("stt", len(pcm)))

        def finish(self):
            calls.append("finalize")
            return []

        def close(self):
            calls.append("tts_retired")

    model = SimpleNamespace(
        identity={},
        stt_stream=Stream,
        tts_stream=lambda text: Stream(),
        tts_next=lambda s: (np.ones(100, np.float32), 24000, 1),
        recognizer=SimpleNamespace(
            child=SimpleNamespace(pid=123, poll=lambda: None),
            cancel=lambda: calls.append("cancel"),
            retire=lambda: calls.append("retire"),
        ),
        adapter=object(),
        endpoint=SimpleNamespace(probability=lambda x: 0.5),
    )
    return model, calls


def test_warm_readiness_requires_real_calls_and_retires_private_state():
    models, calls = models_fixture()
    report = warm_models(models, "windows-pocket")
    assert sum(c[1] for c in calls if isinstance(c, tuple)) == 32000
    assert calls[-4:] == ["finalize", "cancel", "retire", "tts_retired"]
    assert report["models_loaded"] == 3  # no imaginary resident control VAD
    assert report["accelerator"] == "cuda" and report["probe_ms"] > 0
    assert report == models.identity["warm_readiness"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("failure", ["empty", "nan", "rate", "timeout", "stt"])
def test_warm_failure_never_publishes_readiness(failure):
    models, calls = models_fixture()
    if failure == "empty":
        models.tts_next = lambda s: None
    if failure == "nan":
        models.tts_next = lambda s: (np.array([np.nan]), 24000, 1)
    if failure == "rate":
        models.tts_next = lambda s: (np.ones(1), 22050, 1)
    if failure == "stt":
        models.stt_stream = lambda: SimpleNamespace(
            push_audio=lambda x: None, finish=lambda: None
        )
    with pytest.raises((RuntimeError, TimeoutError)):
        warm_models(
            models, "windows-pocket", seconds=-1 if failure == "timeout" else 40
        )
    assert "warm_readiness" not in models.identity
    assert "retire" in calls
    if failure not in {"timeout", "stt"}:
        assert "tts_retired" in calls


def test_evidence_wait_cannot_reuse_an_earlier_session_event():
    host = object.__new__(SDKHost)
    old = {"type": "session_ready", "session_id": "old"}
    new = {"type": "session_ready", "session_id": "new"}
    host.events = [old, new]
    assert host.event("session_ready", session_id="new") is new


def test_private_native_readiness_does_not_claim_cuda():
    models, _ = models_fixture()
    models.identity["backend"] = "windows-pocket-cpu-stt-directml"
    models.recognizer.ready = {
        "backend": "native-directml",
        "providers": {"encoder": ["DmlExecutionProvider"]},
    }
    assert warm_models(models, "windows-pocket")["accelerator"] == "directml"
    models.recognizer.ready["providers"]["encoder"] = ["CPUExecutionProvider"]
    with pytest.raises(RuntimeError, match="provider"):
        warm_models(models, "windows-pocket")


def test_native_tts_reports_separate_accelerators_without_changing_readiness_line():
    models, _ = models_fixture()
    models.identity.update(backend='windows-pocket-vulkan-stt-directml',models={'tts':{'backend':'native-pocket-vulkan'}})
    models.recognizer.ready = {'backend':'native-directml','providers':{'encoder':['DmlExecutionProvider']}}
    report = warm_models(models,'windows-pocket')
    assert report['accelerator'] == 'directml'
    assert report['accelerators'] == {'stt':'directml','tts':'vulkan','endpoint':'cpu'}
    models.identity['models']['tts']['backend'] = 'native-pocket-cpu'
    with pytest.raises(RuntimeError,match='TTS readiness'):
        warm_models(models,'windows-pocket')
