import json
from types import SimpleNamespace

import pytest

from runtime.native_pocket import profile
from runtime.windows_voice import native_pocket


def bound(tmp_path, monkeypatch):
    files = {}
    paths = {"library": "lib/native-pocket/test.dll", "config": "resources/native-pocket/config.yaml"}
    for key, name in paths.items():
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key.encode())
        files[name] = {"bytes": path.stat().st_size, "sha256": profile.digest(path)}
    monkeypatch.setitem(profile.ASSETS, "config.yaml", files[paths["config"]]["sha256"])
    value = {"platform": "windows", "arch": "amd64", "backend": "windows-pocket",
             "stt_backend": "native-directml", "tts_backend": "native-pocket-vulkan",
             "native_pocket": {**paths, "threads": 4, "seed": 17, "max_steps": 750}, "files": files}
    return value


def test_no_selection_preserves_the_approved_engine(tmp_path):
    assert profile.selection(None) is None
    assert profile.selection(tmp_path, {"backend": "windows-pocket", "files": {}}) is None


def test_bound_native_selection(tmp_path, monkeypatch):
    value = bound(tmp_path, monkeypatch)
    (tmp_path/'voice-runtime.json').write_text(json.dumps(value))
    selected = profile.selection(tmp_path)
    assert selected["library"] == tmp_path/'lib/native-pocket/test.dll'
    assert selected["config"] == tmp_path/'resources/native-pocket/config.yaml'


@pytest.mark.parametrize("damage", ["no-selection", "other-backend", "other-stt", "automatic", "two-precisions",
                                    "extra-field", "outside", "unlisted", "bad-bytes", "changed-config",
                                    "bool-thread", "zero-threads", "too-many-threads", "negative-seed", "huge-seed", "zero-steps", "huge-steps"])
def test_native_selection_fails_closed(tmp_path, monkeypatch, damage):
    value = bound(tmp_path, monkeypatch)
    config = value["native_pocket"]
    if damage == "no-selection": del value["tts_backend"]
    elif damage == "other-backend": value["backend"] = "cuda"
    elif damage == "other-stt": value["stt_backend"] = "cuda"
    elif damage == "automatic": value["tts_backend"] = "automatic"
    elif damage == "two-precisions": value["pocket_execution"] = {}
    elif damage == "extra-field": config["noise_file"] = '/unbound'
    elif damage == "outside": config["library"] = '/somewhere/test.dll'
    elif damage == "unlisted": value["files"].pop(config["library"])
    elif damage == "bad-bytes": (tmp_path/config["library"]).write_bytes(b'not the DLL')
    elif damage == "changed-config": monkeypatch.setitem(profile.ASSETS,"config.yaml","0"*64)
    elif damage == "bool-thread": config["threads"] = True
    elif damage == "zero-threads": config["threads"] = 0
    elif damage == "too-many-threads": config["threads"] = 5
    elif damage == "negative-seed": config["seed"] = -1
    elif damage == "huge-seed": config["seed"] = 2**32
    elif damage == "zero-steps": config["max_steps"] = 0
    elif damage == "huge-steps": config["max_steps"] = 751
    with pytest.raises(ValueError):
        profile.selection(tmp_path, value)


@pytest.mark.parametrize("failure", [None, "tts", "endpoint", "stt"])
@pytest.mark.parametrize("use_native_endpoint", [False, True])
def test_native_composition_starts_no_torch_tts_and_retires_every_owner(tmp_path, monkeypatch, failure, use_native_endpoint):
    order = []
    cfg = {"library": tmp_path/'native.dll', "config": tmp_path/'config.yaml', "library_sha256": "bound",
           "threads": 4, "seed": 17, "max_steps": 750}
    monkeypatch.setattr(native_pocket, "selection", lambda _: cfg)
    monkeypatch.setattr(native_pocket, "endpoint_selection", lambda _: None)
    monkeypatch.setattr(native_pocket, "native_endpoint_selection", lambda _: {"bound": True} if use_native_endpoint else None)
    monkeypatch.setattr(native_pocket, "native_catalog", lambda _: (tmp_path/'catalog').resolve())
    monkeypatch.setattr(native_pocket, "sys", SimpleNamespace(platform='win32', executable='bound-python'))
    monkeypatch.setattr(native_pocket.importlib.metadata, 'version', lambda _: 'test')
    monkeypatch.setattr(native_pocket, 'ControlVAD', lambda *a, **k: SimpleNamespace(identity={}))
    # Environment changes are process-local in production; restore them in tests.
    monkeypatch.setenv('GGML_VK_DISABLE_F16', 'test-old')
    monkeypatch.setenv('GGML_VK_VISIBLE_DEVICES', 'test-old')

    class STT:
        def __init__(self, command, log, **options):
            assert command[-1] == '--stt' and command[1:4] == ['-I','-S','-B']
            assert not options['wait_ready'] and options['separate_stderr'] and options['graceful_close']
            order.append('stt_launch')
            self.ready = {'backend': 'native-directml'}

        def await_ready(self):
            order.append('stt_join')
            if failure == 'stt': raise RuntimeError('injected stt')

        def close(self): order.append('stt_close')

    class TTS:
        def __init__(self, library, model_root, **options):
            assert model_root == tmp_path/'tts' and options['config_path'] == tmp_path/'config.yaml'
            order.append('native_load')
            if failure == 'tts': raise RuntimeError('injected tts')
            self.identity = {'backend':'native-pocket-vulkan'}

        def close(self): order.append('native_close')
        def cancel_synthesis(self): order.append('native_cancel')

    def endpoint(*a, **k):
        order.append('endpoint')
        if failure == 'endpoint': raise RuntimeError('injected endpoint')
        return SimpleNamespace(identity={}, startup_seconds={})

    monkeypatch.setattr(native_pocket, 'ResidentSTT', STT)
    monkeypatch.setattr(native_pocket, 'NativePocketBackend', TTS)
    monkeypatch.setattr(native_pocket, 'SmartTurn', endpoint)
    if use_native_endpoint:
        from runtime.native_endpoint import backend

        monkeypatch.setattr(backend, 'NativeEndpoint', endpoint)
        monkeypatch.setattr(native_pocket, 'SmartTurn', lambda *a, **k: pytest.fail('native endpoint fell back to Torch'))
    assets = SimpleNamespace(catalog=tmp_path/'catalog', snapshot=lambda role: tmp_path/role)
    if failure:
        with pytest.raises(RuntimeError, match='injected'):
            native_pocket.WindowsNativePocketModels(tmp_path, None, runtime_root=tmp_path, assets=assets)
        assert 'stt_close' in order
        if failure != 'tts': assert 'native_close' in order
    else:
        model = native_pocket.WindowsNativePocketModels(tmp_path,None,runtime_root=tmp_path,assets=assets)
        assert order == ['stt_launch','native_load','endpoint','stt_join']
        assert model.stt_preroll_frames == 32 and model.stt_preview_policy == 'confirmation_only'
        if use_native_endpoint:
            assert 'torch' not in model.identity['packages'] and 'no Python Torch' in model.identity['torch_scope']
        else:
            assert model.identity['torch_scope'].endswith('no Torch TTS model')
        model.close()
        assert order[-3:] == ['native_cancel','stt_close','native_close']
