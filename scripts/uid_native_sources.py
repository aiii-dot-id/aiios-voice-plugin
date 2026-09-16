"""Validate exact native frontend sources against downloaded public archives.

Copies only build inputs and their licenses; never downloads at build time.
"""

import hashlib
import shutil
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "artifacts/sources/kaldi-native-fbank-1.22.3"
KNF_SHA = "387bf87225c6b83c93ae652eeaef1b4d531994b6e398e7a77189de340674f9af"
KISS_SHA = "497103e664168ebe39580b757adbe616f6cf85a16572af581ca7bc42d0ab13fd"
KNF_NAME = "kaldi-native-fbank-1.22.3"
KISS_NAME = "kissfft-febd4caeed32e33ad8b2e0bb5ea77542c40f18ec"
KNF_CC = [
    "feature-fbank",
    "feature-functions",
    "feature-window",
    "kaldi-math",
    "mel-computations",
    "rfft",
    "log",
]


def verified_sources():
    for name, sha in [("source.tar.gz", KNF_SHA), ("kissfft.zip", KISS_SHA)]:
        if hashlib.sha256((SOURCES / name).read_bytes()).hexdigest() != sha:
            raise ValueError("native source archive changed: " + name)
    files = {}
    with tarfile.open(SOURCES / "source.tar.gz") as archive:
        for entry in archive:
            rel = Path(entry.name).relative_to(KNF_NAME)
            if not entry.isfile():
                continue
            selected = str(rel) == "LICENSE" or (
                rel.parent == Path("kaldi-native-fbank/csrc")
                and (rel.suffix == ".h" or rel.name in {n + ".cc" for n in KNF_CC})
            )
            if selected:
                files["knf/" + str(rel)] = archive.extractfile(entry).read()
    with zipfile.ZipFile(SOURCES / "kissfft.zip") as archive:
        for entry in archive.infolist():
            rel = Path(entry.filename).relative_to(KISS_NAME)
            if str(rel) in {
                "kiss_fft.c",
                "kiss_fftr.c",
                "kiss_fft.h",
                "kiss_fftr.h",
                "_kiss_fft_guts.h",
                "kiss_fft_log.h",
                "COPYING",
                "LICENSES/BSD-3-Clause",
                "LICENSES/Unlicense",
            }:
                files["kiss/" + str(rel)] = archive.read(entry)
    if len([p for p in files if p.endswith(".cc")]) != 7:
        raise ValueError("incomplete Kaldi source set")
    return files


def stage_native(output):
    files = verified_sources()
    output.mkdir(parents=True, exist_ok=False)
    for path, data in files.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for path in (ROOT / "runtime/speaker_identity/native").iterdir():
        if path.is_file():
            shutil.copyfile(path, output / path.name)
    return {p: hashlib.sha256(data).hexdigest() for p, data in files.items()}
