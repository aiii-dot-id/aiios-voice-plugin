"""Isolated entry to a packaged interpreter: no developer/site/PYTHONPATH fallback.

The carrier verifies the bound runtime inventory before launching this file.
This module deliberately imports only the standard library until it has checked
the interpreter and installed the two explicit packaged code paths. Models are
provided as data through AII_MODELS_DIR; they are never added to sys.path.
"""

import contextlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import runpy
import sys
from pathlib import Path

DLL_HANDLES = []


# Import exactly one carrier-inventory-bound, stdlib-only helper by its own
# path. Do not admit engine/dependency directories to sys.path before prepare
# has established the packaged interpreter and its import-path containment.
_path_spec = importlib.util.spec_from_file_location(
    "_aii_physical_paths", Path(__file__).parent.parent / "runtime/physical_paths.py"
)
_paths = importlib.util.module_from_spec(_path_spec)
_path_spec.loader.exec_module(_paths)
windows_path_identity = _paths.windows_path_identity
path_identity = _paths.path_identity
existing_io_path = _paths.existing_io_path


def prepare(*, stt=False):
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise RuntimeError("packaged interpreter requires -I -S -B")
    root = existing_io_path(__file__).parents[2]
    profile = json.loads((root / "voice-runtime.json").read_text())
    python_root = existing_io_path(root / "python")
    python_identity = path_identity(python_root)
    if path_identity(sys.base_prefix) != python_identity:
        raise RuntimeError("interpreter escaped its packaged prefix")
    for entry in sys.path:
        path_identity(entry).relative_to(python_identity)
    engine = root / "engine"
    names = (
        profile["stt_sites"]
        if stt
        else [profile["site"], *profile.get("extra_sites", [])]
    )
    sites = [existing_io_path(root / name) for name in names]
    for site in sites:
        path_identity(site).relative_to(path_identity(root))
        if not site.is_dir():
            raise RuntimeError("packaged import directory missing")
    sys.path[:0] = [str(engine), *map(str, sites)]
    if sys.platform == "win32":
        # Retain handles: dropping an add_dll_directory object closes the search
        # directory. No development PATH or Conda DLL directory is admitted.
        for path in [
            python_root / "DLLs",
            python_root / "Library/bin",
            *[s / "torch/lib" for s in sites],
        ]:
            if path.is_dir():
                DLL_HANDLES.append(os.add_dll_directory(str(path)))
    data = os.environ.get("AII_MODELS_DIR", "")
    if not data or not Path(data).is_dir():
        raise RuntimeError("host-provided AII_MODELS_DIR is required")
    if profile["backend"] == "mlx":
        # Torch eager imports initialize a compiler-cache path even though this
        # MLX inference path never compiles Torch graphs. Use the existing,
        # bound runtime directory instead of probing/creating ambient temp.
        # The read-only runtime wall still refuses any actual cache write.
        os.environ["TORCHINDUCTOR_CACHE_DIR"] = str(root)
    return root, profile, existing_io_path(data)


def module_files(root, modules):
    root_identity = path_identity(root)
    files = {}
    for name, module in modules.items():
        # torch.ops/classes synthesize attributes, including __file__, through
        # __getattr__. Inspect actual module storage, never execute that hook.
        path = vars(module).get("__file__")
        if path:
            aliases = {"torch.ops": "_ops.py", "torch.classes": "_classes.py"}
            if name in aliases:
                # Torch's two facades carry labels, not filesystem locations.
                # Attribute them to the real implementation; never resolve a
                # label relative to the process working directory.
                if path != aliases[name]:
                    raise ValueError("unexpected Torch facade source label")
                owner = modules.get("torch." + path.removesuffix(".py"))
                package = modules.get("torch")
                source = vars(owner).get("__file__") if owner is not None else None
                package_source = (
                    vars(package).get("__file__") if package is not None else None
                )
                if not source or not package_source:
                    raise ValueError("Torch facade has no implementation owner")
                resolved = path_identity(source, strict=True)
                if resolved != path_identity(package_source, strict=True).parent / path:
                    raise ValueError("Torch facade implementation differs")
                files[name] = str(resolved.relative_to(root_identity))
                continue
            resolved = path_identity(path)
            files[name] = str(resolved.relative_to(root_identity))
    return files


def native_images(root):
    """Record loaded images, allowing only packaged or operating-system code."""
    import ctypes

    root_identity = path_identity(root)

    if sys.platform == "linux":
        images = set()
        for line in Path("/proc/self/maps").read_text().splitlines():
            parts = line.split(maxsplit=5)
            if len(parts) != 6 or "x" not in parts[1] or not parts[5].startswith("/"):
                continue
            if parts[5].endswith(" (deleted)"):
                raise RuntimeError("executed native image was deleted")
            path = Path(parts[5]).resolve(strict=True)
            if not (
                path.is_relative_to(root)
                or path.is_relative_to("/usr/lib")
                or path.is_relative_to("/lib")
            ):
                raise RuntimeError(f"native dependency escaped runtime/system: {path}")
            images.add(str(path))
        return sorted(images)
    if sys.platform == "win32":
        from ctypes import wintypes

        psapi = ctypes.WinDLL("psapi")
        kernel = ctypes.WinDLL("kernel32")
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        process = kernel.GetCurrentProcess()
        modules = (wintypes.HMODULE * 2048)()
        needed = wintypes.DWORD()
        enum = psapi.EnumProcessModules
        enum.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.HMODULE),
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        if not enum(
            process, modules, ctypes.sizeof(modules), ctypes.byref(needed)
        ) or needed.value > ctypes.sizeof(modules):
            raise RuntimeError("cannot enumerate complete native image set")
        get_name = psapi.GetModuleFileNameExW
        get_name.argtypes = [
            wintypes.HANDLE,
            wintypes.HMODULE,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        system = path_identity(os.environ["SystemRoot"])
        images = []
        for handle in modules[: needed.value // ctypes.sizeof(wintypes.HMODULE)]:
            name = ctypes.create_unicode_buffer(32768)
            length = get_name(process, handle, name, len(name))
            if not length or length >= len(name):
                raise RuntimeError("native image path unavailable or truncated")
            path = existing_io_path(name.value)
            identity = path_identity(path)
            if not any(
                identity.is_relative_to(p)
                for p in (root_identity, system / "System32", system / "WinSxS")
            ):
                raise RuntimeError(f"native dependency escaped runtime/system: {path}")
            images.append(str(path))
        return images
    lib = ctypes.CDLL(None)
    count = lib._dyld_image_count
    count.restype = ctypes.c_uint32
    name = lib._dyld_get_image_name
    name.argtypes = [ctypes.c_uint32]
    name.restype = ctypes.c_char_p
    images = []
    for index in range(count()):
        path = Path(os.fsdecode(name(index))).resolve()
        if not (
            path.is_relative_to(root)
            or path.is_relative_to("/usr/lib")
            or path.is_relative_to("/System/Library")
        ):
            raise RuntimeError(f"native dependency escaped runtime/system: {path}")
        images.append(str(path))
    return images


def main():
    stt = sys.argv[1:2] in (["--stt"], ["--inspect-stt"])
    root, profile, data = prepare(stt=stt)
    from runtime.stt.profile import native_catalog

    native = native_catalog(root, profile)
    native_tts = None
    if "tts_backend" in profile or "native_pocket" in profile:
        from runtime.native_pocket.profile import selection

        native_tts = selection(root, profile)
    from runtime.voice_core.endpoint_profile import selection as endpoint_selection

    frontend = endpoint_selection(root, profile)
    from runtime.native_endpoint.profile import selection as native_endpoint_selection

    native_endpoint = native_endpoint_selection(root, profile)
    if sys.argv[1:] in (["--inspect"], ["--inspect-stt"]):
        # Imports may initialize accelerator libraries, but do not load weights.
        names = (
            ("numpy", "onnxruntime")
            if stt and native is not None
            else (
                ("mlx.core", "numpy", "onnxruntime", "torch")
                if profile["backend"] == "mlx"
                else (("numpy", "onnxruntime") if native_endpoint is not None else
                      ("numpy", "torch") if frontend is not None else ("numpy", "torch", "transformers"))
            )
        )
        with contextlib.redirect_stdout(sys.stderr):
            for name in names:
                importlib.import_module(name)
            if not stt:
                if native_endpoint is not None:
                    from runtime.native_endpoint.backend import bind, open_library

                    library = open_library(native_endpoint["library"])
                    bind(library)
                    DLL_HANDLES.append(library)
                elif frontend is None:
                    _ = importlib.import_module("transformers").WhisperFeatureExtractor
                else:
                    from runtime.voice_core.whisper_torch_frontend import (
                        TorchWhisperFrontend,
                    )

                    _ = TorchWhisperFrontend(frontend["coefficients"], frontend["sha256"])
                if sys.platform == "win32":
                    if native_tts is None:
                        importlib.import_module("pocket_tts")
                    else:
                        # Inspect this lane's real library without loading any
                        # weights. The selected endpoint implementation above
                        # establishes whether Python Torch is needed.
                        import ctypes

                        DLL_HANDLES.append(ctypes.CDLL(str(native_tts["library"])))
                    importlib.import_module("onnxruntime")
                elif profile["backend"] == "cuda":
                    importlib.import_module("qwen_tts")
                    importlib.import_module("onnxruntime")
        version_names = (
            ("numpy", "onnxruntime-directml")
            if stt and native is not None
            else (
                ("mlx", "mlx-audio", "numpy", "onnxruntime", "torch", "transformers")
                if profile["backend"] == "mlx"
                else (("numpy", "onnxruntime-directml") if native_endpoint is not None else
                      ("numpy", "torch") if frontend is not None else ("numpy", "torch", "transformers"))
            )
        )
        print(
            json.dumps(
                {
                    "isolated": True,
                    "prefix": str(sys.base_prefix),
                    "modules": module_files(root, dict(sys.modules)),
                    "native_images": native_images(root),
                    "versions": {
                        name: importlib.metadata.version(name) for name in version_names
                    },
                }
            )
        )
        return
    if stt:
        if native is not None:
            if sys.argv[1:] != ["--stt"]:
                raise RuntimeError(
                    "native packaged STT takes no external selection arguments"
                )
            sys.argv = [
                "packaged-stt",
                "--model-assets",
                str(native),
                "--root",
                str(data),
            ]
            runpy.run_module("runtime.stt.native_resident", run_name="__main__")
            return
        sys.argv = ["packaged-stt", *sys.argv[2:]]
        runpy.run_module("runtime.stt.cuda_resident", run_name="__main__")
        return
    if sys.argv[1:]:
        raise RuntimeError("unknown packaged bootstrap argument")
    sys.argv = ["voice-engine", "--root", str(data), "--backend", profile["backend"]]
    if "model_assets" in profile:
        name = profile["model_assets"]
        if name not in profile["files"] or not name.startswith(
            "resources/model-assets/"
        ):
            raise RuntimeError("model catalog must belong to the verified runtime")
        catalog = existing_io_path(root / name)
        path_identity(catalog).relative_to(path_identity(root / "resources/model-assets"))
        sys.argv += ["--model-assets", str(catalog)]
    if profile["backend"] == "windows-pocket":
        sys.argv += [
            "--stage",
            str(data),
            "--pocket-root",
            str(data / "pocket"),
            "--runtime-root",
            str(root),
        ]
    elif profile["backend"] == "cuda":
        sys.argv += ["--stage", str(data), "--runtime-root", str(root)]
    runpy.run_module("runtime.plugin_engine.worker", run_name="__main__")


if __name__ == "__main__":
    main()
