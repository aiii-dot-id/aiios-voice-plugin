"""Derive the pinned native I/O path patch, without machine-local build inputs."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "tests/fixtures/native_path_upstream"
PINS = {
    "src/framework/assets/resource_bundle.cpp": "af49c2734b84b6dd96c8ca102737df4e7eb1c543d0c8a9158759faaffac2f304",
    "src/framework/io/filesystem.cpp": "bd2bf22a516c7046e1e95d51a46b8812bcf0960f3cf37e8ead413ea8748ee700",
    "src/framework/io/safetensors.cpp": "36dbb50bc90429131ee169a3cdaf771e1a5c3ead2e9024f658ff7a89c4c9cea3",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def derive(original):
    if set(original) != set(PINS) or any(sha(original[n]) != h for n, h in PINS.items()):
        raise ValueError("native upstream binding differs")
    result = {}
    counts = {"resource_bundle.cpp": 2, "filesystem.cpp": 4, "safetensors.cpp": 1}
    for n, raw in original.items():
        name = Path(n).name
        text = raw.decode()
        if name == "resource_bundle.cpp":
            old = "std::filesystem::weakly_canonical(path).generic_string()"
            if text.count(old) != 1:
                raise ValueError("physical cache-key site differs")
            text = text.replace(old, "aii::platform::physical_key(path)")
        old = "std::filesystem::weakly_canonical(path)"
        if text.count(old) != counts[name]:
            raise ValueError("existing I/O path sites differ")
        text = '#include "paths.h"\n' + text.replace(old, "aii::platform::existing_io_path(path)")
        result[name] = text.encode()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    changed = derive({n: (args.upstream / n).read_bytes() for n in PINS})
    changed["paths.h"] = (ROOT / "runtime/native/platform/paths.h").read_bytes()
    binding = {"upstream": PINS, "candidate": {n: sha(b) for n, b in changed.items()}}
    # Refuse an existing destination. No old candidate or build inputs are overwritten.
    args.output.mkdir(parents=True, exist_ok=False)
    for name, raw in changed.items():
        (args.output / name).write_bytes(raw)
    (args.output / "path-shim-binding.json").write_text(json.dumps(binding, indent=2) + "\n")


if __name__ == "__main__":
    main()
