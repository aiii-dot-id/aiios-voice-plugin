"""Keep pre-spawn verification out of the process readiness measurement."""

from types import SimpleNamespace

import pytest

from scripts.prove_plugin_sdk_engine import SDKHost


def test_readiness_keeps_distinct_host_and_process_clocks(tmp_path, monkeypatch):
    log = tmp_path / "ready.log"
    log.write_text(
        "AII_VOICE_READY event=ready models_loaded=3 accelerator=cuda probe_ms=900\n"
    )
    host = SDKHost.__new__(SDKHost)
    host.started = 100.0
    host.startup_timing = {
        "host_runtime_verification_seconds": 170.0,
        "carrier_spawn_begin_elapsed": 172.0,
        "carrier_spawn_return_elapsed": 172.01,
    }
    host.log = SimpleNamespace(name=str(log))
    monkeypatch.setattr(
        "scripts.prove_plugin_sdk_engine.time.perf_counter", lambda: 305.0
    )
    result = host.readiness()
    assert result["observed_elapsed"] == 205.0
    assert result["host_startup_timing"]["spawn_to_ready_seconds"] == 33.0
    assert result["host_startup_timing"]["host_runtime_verification_seconds"] == 170.0
    assert result["probe_ms"] == 900
    log.write_text(log.read_text() * 2)
    with pytest.raises(ValueError, match="duplicate public SDK readiness"):
        host.readiness()
