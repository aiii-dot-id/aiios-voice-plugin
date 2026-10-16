"""Explicit, inventory-bound selection of the exact Windows native endpoint."""

from runtime.model_assets import checked, file_hashes, read_json
from runtime.physical_paths import existing_io_path


def selection(root, profile=None):
    if root is None:
        return None
    root = existing_io_path(root)
    if profile is None:
        profile, _ = read_json(checked(root, "voice-runtime.json"), max_bytes=32 * 1024**2)
    if "endpoint_backend" not in profile:
        if "native_endpoint" in profile:
            raise ValueError("native endpoint lacks explicit backend selection")
        return None
    if (profile.get("platform"), profile.get("arch"), profile.get("backend"),
        profile.get("stt_backend"), profile.get("tts_backend"), profile["endpoint_backend"]) != (
        "windows", "amd64", "windows-pocket", "native-directml", "native-pocket-vulkan", "native-aten-cpu"
    ) or "endpoint_frontend" in profile or "pocket_execution" in profile:
        raise ValueError("unsupported or conflicting native endpoint composition")
    config = profile.get("native_endpoint")
    if not isinstance(config, dict) or set(config) != {"library", "coefficients"}:
        raise ValueError("native endpoint fields differ")
    library, coefficients = config["library"], config["coefficients"]
    if library != "lib/native-endpoint/aii_native_endpoint.dll" or coefficients != "resources/endpoint/windows-coefficients.f32":
        raise ValueError("native endpoint paths must name the runtime-owned component")
    files = profile["files"]
    names = {n for n in files if n.startswith("lib/native-endpoint/")}
    if library not in names or not 2 <= len(names) <= 32 or coefficients not in files:
        raise ValueError("native endpoint component inventory is incomplete")
    if any(n.count("/") != 2 or not n.endswith(".dll") for n in names):
        raise ValueError("native endpoint directory must contain only bound libraries")
    actual = {"lib/native-endpoint/" + p.name for p in checked(root, "lib/native-endpoint", directory=True).iterdir()}
    if actual != names:
        raise ValueError("unlisted or missing native endpoint library")
    paths = {}
    for name in names | {coefficients}:
        row = files[name]
        if type(row.get("bytes")) is not int or not 0 < row["bytes"] <= 512 * 1024**2:
            raise ValueError("invalid native endpoint file extent")
        path = checked(root, name)
        if file_hashes(path, row["bytes"])[0] != row["sha256"]:
            raise ValueError("native endpoint file hash differs")
        paths[name] = path
    if files[coefficients]["bytes"] != 65920:
        raise ValueError("native endpoint coefficients have the wrong extent")
    return {"library": paths[library], "coefficients": paths[coefficients],
            "files": {n: files[n] for n in names | {coefficients}}, "root": root,
            "library_sha256": files[library]["sha256"], "coefficients_sha256": files[coefficients]["sha256"]}
