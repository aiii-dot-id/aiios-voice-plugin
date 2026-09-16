import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from runtime.native_pocket.backend import ASSETS, NativePocketBackend, selected_assets, wire_text


def test_voice_selection_checks_exact_asset_and_never_defaults():
    assert selected_assets("alba", None) == ASSETS
    choices = {"alba": ASSETS["embeddings/alba.safetensors"], "marius": "a" * 64}
    selected = selected_assets("marius", choices)
    assert selected["embeddings/marius.safetensors"] == "a" * 64
    assert "embeddings/alba.safetensors" not in selected
    choices["marius"] = "b" * 64
    assert selected["embeddings/marius.safetensors"] == "a" * 64


@pytest.mark.parametrize("voice,choices", [
    ("marius", None), ("missing", {"alba": "0" * 64}),
    ("alba", {"alba": "0" * 64}), ("../marius", {"../marius": "a" * 64}),
    ("marius", {"marius": "not-a-hash"}), ("marius", {}),
    ("marius", {"marius": "a" * 64, "bad/path": "b" * 64}),
])
def test_unbound_changed_or_path_voice_is_refused(voice, choices):
    with pytest.raises(ValueError):
        selected_assets(voice, choices)


class FakeNative:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancelled = []
        self.fail = False
        self.resets = self.destroyed = 0

    def nv_start(self, *args):
        return 0

    def nv_next(self, handle, generation, buffer, capacity, count, error, size):
        self.entered.set()
        assert self.release.wait(2), "test owner not released"
        if self.fail:
            error.value = b"actual model fault"
            return -1
        buffer[0] = .5
        count._obj.value = 1
        return 1

    def nv_cancel(self, handle, generation):
        self.cancelled.append(generation)
        return 0

    def nv_reset(self, *args):
        self.resets += 1
        return 0

    def nv_destroy(self, *args):
        self.destroyed += 1
        return 0


def backend():
    b = NativePocketBackend.__new__(NativePocketBackend)
    b._lifetime, b._owner = threading.Lock(), threading.Lock()
    b._active, b._handle, b._generation = None, 1, 0
    b._closed = b._failed = False
    b._dll_directory = None
    b.seed, b.max_steps, b.noise_file = 17, 750, None
    b._lib = FakeNative()
    return b


@pytest.mark.parametrize("text", ["", "  ", None, "x"*513, "hello\0world", "\ud800"])
def test_invalid_text_refused(text):
    with pytest.raises((ValueError, UnicodeError)):
        wire_text(text)


def test_unicode_text_preserved():
    assert wire_text("Good morning — café.").decode() == "Good morning — café."


def test_cancel_does_not_wait_and_late_pcm_cannot_escape():
    b = backend()
    stream = b.tts_stream("hello")
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(stream.next)
        assert b._lib.entered.wait(1)
        try:
            b.cancel_synthesis()
            assert b._lib.cancelled == [1] and not pending.done()
            with pytest.raises(RuntimeError, match="in-flight"):
                stream.close()
            assert b._active is stream
            with pytest.raises(RuntimeError):
                b.close()
            assert b._lib.destroyed == 0
        finally:
            b._lib.release.set()
        assert pending.result() is None
    stream.close()
    assert b._active is None
    recovered = b.tts_stream("new generation")
    assert recovered.generation == 2 and recovered.next()[0].tolist() == [.5]
    recovered.close()
    b.close()
    assert b._lib.destroyed == 1


def test_model_fault_is_not_laundered_into_cancelled_success():
    b = backend()
    b._lib.fail = True
    stream = b.tts_stream("hello")
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(stream.next)
        assert b._lib.entered.wait(1)
        b.cancel_synthesis()
        b._lib.release.set()
        with pytest.raises(RuntimeError, match="actual model fault"):
            pending.result()
    stream.close()
    with pytest.raises(RuntimeError, match="failed"):
        b.tts_stream("must not reuse")
    b.close()


def test_active_stream_and_closed_handle_have_one_owner():
    b = backend()
    stream = b.tts_stream("hello")
    with pytest.raises(RuntimeError, match="active"):
        b.tts_stream("collision")
    stream.close()
    stream.close()
    b.close()
    assert stream.next() is None
    stream.close()
    b.close()
    assert b._lib.destroyed == 1 and b._lib.resets == 1


def test_admission_owner_collision_does_not_leave_a_phantom_stream():
    b = backend()
    b._owner.acquire()
    try:
        with pytest.raises(RuntimeError, match="owner"):
            b.tts_stream("collision before native admission")
        assert b._active is None and b._failed
    finally:
        b._owner.release()
    b.close()
    assert b._lib.destroyed == 1


@pytest.mark.parametrize('failure',['load','symbol'])
def test_failed_library_load_retires_the_temporary_dll_search_directory(tmp_path,monkeypatch,failure):
    from types import SimpleNamespace

    from runtime.native_pocket import backend as module

    closed = []
    # Reach the actual DLL-load seam, not a missing-file failure during the
    # preceding physical-path check (which also raises OSError).
    (tmp_path/'native.dll').write_bytes(b'fixture library')
    loaded = []
    monkeypatch.setattr(module,'digest',lambda p: 'binary' if p.name == 'native.dll' else module.ASSETS[p.name if p.name != 'alba.safetensors' else 'embeddings/alba.safetensors'])
    monkeypatch.setattr(module,'os',SimpleNamespace(name='nt',environ={'GGML_VK_DISABLE_F16':'1','GGML_VK_VISIBLE_DEVICES':'0'},
                                                 add_dll_directory=lambda p: SimpleNamespace(close=lambda:closed.append(p))))
    def library(path):
        loaded.append(str(path))
        if failure == 'load': raise OSError('missing runtime DLL')
        return object()  # A valid library object missing the required symbol.
    monkeypatch.setattr(module.C,'CDLL',library)
    with pytest.raises((OSError,AttributeError)):
        NativePocketBackend(tmp_path/'native.dll',tmp_path,binary_sha256='binary')
    assert closed == [str(tmp_path)]
    assert loaded == [str(tmp_path/'native.dll')]
