"""Replay a build-verified lazy-import registry for a sealed Transformers tree.

No model, export, backend condition or dependency is removed. The standard
generator remains available for paths outside the frozen models subtree.
Only a manifest-declared, exact-source-bound registry enables this adapter.
"""

import copy
import functools
import hashlib
import importlib.machinery
import json
import sys
from pathlib import Path, PurePosixPath

from runtime.physical_paths import path_identity

UTILITY_SHA = "2a66324d49f7ed730a92288e439c736259f67c290af23d5df84803e08c21e6b9"
SCHEMA = "aiii.voice.frozen-transformers-imports"
OBSERVATIONS = {"hits": [], "fallbacks": []}


def pack(value):
    if isinstance(value, dict):
        pairs = []
        for key, item in value.items():
            if isinstance(key, str):
                k = ["path", key]
            elif isinstance(key, frozenset) and all(isinstance(x, str) for x in key):
                k = ["backends", sorted(key)]
            else:
                raise ValueError("unsupported registry key")
            pairs.append([k, pack(item)])
        # Keep insertion order: duplicate export resolution must not reorder.
        return ["dict", pairs]
    if isinstance(value, set) and all(isinstance(x, str) for x in value):
        return ["exports", sorted(value)]
    raise ValueError("unsupported registry value")


def unpack(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("invalid registry value")
    tag, items = value
    if not isinstance(items, list):
        raise TypeError("invalid registry items")
    if tag == "exports":
        if not all(isinstance(x, str) for x in items) or len(set(items)) != len(items):
            raise ValueError("invalid registry exports")
        return set(items)
    if tag != "dict":
        raise ValueError("invalid registry tag")
    result = {}
    for key, item in items:
        kind, data = key
        if kind == "path" and isinstance(data, str):
            k = data
        elif (
            kind == "backends"
            and isinstance(data, list)
            and all(isinstance(x, str) for x in data)
        ):
            if len(set(data)) != len(data):
                raise ValueError("duplicate backend requirement")
            k = frozenset(data)
        else:
            raise ValueError("invalid registry key")
        if k in result:
            raise ValueError("duplicate registry key")
        result[k] = unpack(item)
    return result


def tree_digest(files, package):
    selected = {
        name: row["sha256"]
        for name, row in files.items()
        if name.startswith(package + "/") and name.endswith(".py")
    }
    if not selected:
        raise ValueError("no bound Transformers source")
    return hashlib.sha256(
        json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def member(root, name):
    path = PurePosixPath(name)
    if (
        not name
        or str(path) != name
        or path.is_absolute()
        or ".." in path.parts
        or ":" in name
        or "\\" in name
    ):
        raise ValueError("unsafe registry path")
    target = root / name
    path_identity(target, strict=True).relative_to(path_identity(root, strict=True))
    if target.is_symlink():
        raise ValueError("linked registry path")
    return target


class RewriteLoader:
    def __init__(self, original, rewrite):
        self.original, self.rewrite = original, rewrite

    def create_module(self, spec):
        return self.original.create_module(spec)

    def exec_module(self, module):
        self.original.exec_module(module)
        self.rewrite(module)

    def __getattr__(self, name):
        return getattr(self.original, name)


class UtilityFinder:
    """Intercept exactly one known source file after its ordinary execution."""

    def __init__(self, utility, rewrite):
        self.utility, self.rewrite = utility, rewrite
        self.identity = path_identity(utility, strict=True)

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "transformers.utils.import_utils":
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if (
            spec is None
            or spec.loader is None
            or path_identity(spec.origin, strict=True) != self.identity
        ):
            raise ImportError("Transformers utility escaped bound package")
        if hashlib.sha256(self.utility.read_bytes()).hexdigest() != UTILITY_SHA:
            raise ImportError("Transformers import-registry API changed")
        spec.loader = RewriteLoader(spec.loader, self.rewrite)
        # One import owns this adapter; later importers use the actual module.
        sys.meta_path.remove(self)
        return spec


def replay_generator(original, models, structure, observations):
    models = path_identity(models)

    @functools.lru_cache
    def generate(module_path):
        path = Path(module_path)
        if path.is_file():
            path = path.parent
        path = path_identity(path)
        try:
            parts = path.relative_to(models).parts
        except ValueError:
            observations["fallbacks"].append(str(path))
            return original(module_path)
        value = structure
        for part in parts:
            if part not in value or not isinstance(value[part], dict):
                # Do not silently turn an unknown model into an empty export set.
                raise ImportError("model subtree missing from sealed registry")
            value = value[part]
        observations["hits"].append("/".join(parts) or ".")
        # Neither a caller nor the upstream spread/update pass may mutate the
        # authoritative frozen tree or another module's registry.
        return copy.deepcopy(value)

    return generate


def install(root, profile):
    binding = profile.get("frozen_transformers_imports")
    if not binding:
        return False
    if (
        "transformers" in sys.modules
        or "transformers.utils.import_utils" in sys.modules
    ):
        raise RuntimeError("registry adapter must precede Transformers imports")
    package = binding["package"]
    catalog_path = member(root, binding["catalog"])
    if catalog_path.stat().st_size > 4 * 1024**2:
        raise ValueError("registry exceeds bounded input size")
    raw = catalog_path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if (
        sha != binding["catalog_sha256"]
        or profile["files"][binding["catalog"]]["sha256"] != sha
    ):
        raise ValueError("registry binding differs")
    body = json.loads(raw)
    utility_name = package + "/utils/import_utils.py"
    if (
        body["schema"] != SCHEMA
        or body["utility_sha256"] != UTILITY_SHA
        or profile["files"][utility_name]["sha256"] != UTILITY_SHA
        or body["package_tree_sha256"] != tree_digest(profile["files"], package)
    ):
        raise ValueError("registry source binding differs")
    structure = unpack(body["structure"])
    models = member(root, package + "/models")

    def rewrite(module):
        module.create_import_structure_from_path = replay_generator(
            module.create_import_structure_from_path, models, structure, OBSERVATIONS
        )

    sys.meta_path.insert(0, UtilityFinder(member(root, utility_name), rewrite))
    return True
