import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GO_MOD = ROOT / "plugin" / "native" / "go.mod"


def read_version(go_mod=GO_MOD):
    named = re.search(r"(?m)^go (\d+\.\d+\.\d+)$", Path(go_mod).read_text(encoding="utf-8"))
    if not named:
        raise ValueError(f"{go_mod} does not name an exact Go version on its go line")
    return named.group(1)


VERSION = read_version()


def default_go():
    return Path(os.environ.get("AII_GO") or f"/usr/local/go{VERSION}/bin/go")


def require(go, what):
    version = subprocess.check_output([str(go), "version"], text=True).strip()
    if f"go{VERSION} " not in version:
        raise ValueError(f"{what} requires Go {VERSION} (the go line of plugin/native/go.mod); {go} is {version}")
    return version
