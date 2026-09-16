"""No writable runtime/configuration directory is required by SDK model loading."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.plugin_engine.worker import load_models, native_candidate_options
from runtime.speech_output.pocket_loading import load_bound_model


def test_bound_pocket_factory_keeps_file_load_defaults(monkeypatch):
    config = {"default_temperature": 0.37, "weights_path": "/bound/model"}
    validated, calls = [], []

    def validate(**fields):
        validated.append(fields)
        return SimpleNamespace(**fields)

    expected = object()
    api = SimpleNamespace(
        _from_pydantic_config_with_weights=lambda *a, **k: (
            calls.append((a, k)) or expected
        )
    )
    monkeypatch.setitem(
        sys.modules, "pocket_tts.utils.config", SimpleNamespace(Config=validate)
    )
    monkeypatch.setitem(
        sys.modules,
        "pocket_tts.models.tts_model",
        SimpleNamespace(
            TTSModel=api,
            DEFAULT_EOS_THRESHOLD=-0.43,
            DEFAULT_NOISE_CLAMP=0.8,
            DEFAULT_SAMPLER_DECODE_STEPS=3,
        ),
    )
    assert load_bound_model(config) is expected
    assert validated == [config]
    args, kwargs = calls[0]
    assert vars(args[0]) == config
    assert args[1:] == (0.37, 3, 0.8, -0.43)
    assert kwargs == {"origin": None}


@pytest.mark.parametrize("backend", ["cuda", "windows-pocket"])
@pytest.mark.parametrize("named_state", [False, True])
def test_sdk_loader_never_creates_or_requires_state_directory(
    tmp_path, monkeypatch, backend, named_state
):
    calls = []
    model = object()

    def construct(*args, **kwargs):
        calls.append((args, kwargs))
        return model

    from runtime.cuda_voice import backend as cuda
    from runtime.windows_voice import pocket

    monkeypatch.setattr(cuda, "CUDAModels", construct)
    monkeypatch.setattr(pocket, "WindowsPocketModels", construct)

    def refuse_mkdir(*args, **kwargs):
        raise PermissionError("read-only installed engine")

    monkeypatch.setattr(Path, "mkdir", refuse_mkdir)
    state = tmp_path / "must-not-exist" if named_state else None
    args = SimpleNamespace(
        backend=backend,
        root=tmp_path,
        stage=tmp_path,
        state_dir=state,
        pocket_root=tmp_path,
    )
    assert load_models(args, None) is model
    assert calls[0][1]["record_observations"] is False
    assert state is None or not state.exists()


def test_private_native_options_reach_real_loader(monkeypatch, tmp_path):
    from runtime.windows_voice import pocket

    calls = []
    monkeypatch.setattr(pocket, "WindowsPocketModels", lambda *a, **k: calls.append(k))
    args = SimpleNamespace(
        backend="windows-pocket",
        root=tmp_path,
        stage=tmp_path,
        state_dir=None,
        pocket_root=tmp_path,
        native_stt_root=tmp_path / "native",
        native_stt_python=tmp_path / "python",
    )
    load_models(args, None)
    assert calls[0]["native_stt_root"] == args.native_stt_root
    assert calls[0]["native_stt_python"] == args.native_stt_python
    assert calls[0]["record_observations"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"native_stt_root": None},
        {"native_stt_python": None},
        {"backend": "cuda"},
        {"backend": "mlx"},
        {"packaged_runtime": True},
        {"fixture": True},
        {"runtime_root": "/sealed"},
        {"model_assets": "/models"},
    ],
)
def test_native_candidate_cannot_silently_select_other_models(change):
    args = SimpleNamespace(
        **(
            {
                "backend": "windows-pocket",
                "native_stt_root": "/native",
                "native_stt_python": "/python",
            }
            | change
        )
    )
    with pytest.raises(ValueError):
        native_candidate_options(args)
