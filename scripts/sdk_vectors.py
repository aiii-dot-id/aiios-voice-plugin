import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "tests/vectors/sdk_regressions.json"


def _go():
    from scripts import go_toolchain
    return shutil.which("go") or str(go_toolchain.default_go())


def vector(sdk, name, go=None):
    sdk = Path(sdk)
    own = sdk / "vectors" / name
    if own.is_file():
        return own.read_bytes()
    try:
        proven = json.loads(RECORD.read_text())
        pin = json.loads((ROOT / "plugin/sdk-source.json").read_text())
        want = proven["vectors_sha256"][name]
        if proven["pinned_revision"] != pin["revision"]:
            return None
        for package, digest in proven["code_sha256"].items():
            if not (sdk / package).is_dir():
                return None
            run = subprocess.run([str(go or _go()), "run", str(ROOT / "scripts/sdk_code_digest.go"), str(sdk / package)],
                                 cwd=ROOT, capture_output=True, text=True, timeout=600, env=dict(os.environ, GOWORK="off"))
            if run.returncode != 0 or run.stdout.split()[:1] != [digest]:
                return None
        raw = (ROOT / "tests/vectors" / name).read_bytes()
    except (OSError, ValueError, KeyError):
        return None
    return raw if hashlib.sha256(raw).hexdigest() == want else None
