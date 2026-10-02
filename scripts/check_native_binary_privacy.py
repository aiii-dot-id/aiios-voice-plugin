"""Read-only privacy check of every shipped native image and package metadata.

Inspect ASCII and UTF-16 strings, including compiler macros, linker paths and
CodeView references. Report categories/counts, never matched private values.
This is not an audit of arbitrary personal names, certificates or model data.
Review vendor notices and all final archive metadata separately.

Default deny: every native image in a runtime is scanned, recognized or not.
Only images declared third-party by exact bytes may carry their vendor's own
build roots, and those findings are reported rather than hidden. An explicit
private prefix or credential pattern fails in any image.
"""
import argparse
import hashlib
import json
import mmap
import os
from pathlib import Path
import re

from scripts.check_public_privacy import PATTERNS

# A build or scratch root anywhere in a string. The first group is the
# historical set; later roots must start a path (not continue a word, URL or
# relative path) so ordinary strings such as "tree/root/x" are not findings.
_START = r'(?:(?<![\w.~-])|(?<=-[ILF]))'
ROOT = re.compile(
    r'(?i)(?:[/](?:Users|Volumes|home|private[/]tmp|tmp)[/]|[/]work[/]'
    r'|[a-z]:[/](?:Users|work|Windows[/]Temp|tmp|build|aiii[^/]*)[/]'
    r'|' + _START + r'[/](?:var[/]folders|private[/]var|root|opt|mnt|srv)[/]'
    r'|' + _START + r'[a-z]:[/](?:temp|proof|a|actions-runner|agent|hostedtoolcache)[/])')
SECRET_CATEGORIES = ('private-key', 'provider-token', 'jwt', 'credential-url')
# A third-party image keeps its vendor's build strings; these never pass.
ALWAYS_FAIL = ('explicit-private-prefix',) + SECRET_CATEGORIES
# Pinned ggml's optional GPU trace output template is a public runtime path,
# not an input/build location. Exact equality only; added text is still checked.
PUBLIC_RUNTIME_PATHS = frozenset(('/tmp/perf-metal-%d.gputrace',))
IMAGE_MAGIC = (b'\x7fELF', b'MZ', b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf',
               b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf')
IMAGE_NAME = re.compile(r'(?i)\.(?:dll|exe|dylib|so|pyd|node)(?:\.\d+)*$')
THIRD_PARTY_SCHEMA = 'aiii.voice.third-party-images.v1'
# Default deny scans vendor GPU libraries too; some exceed the old 512 MiB
# bound. Images are mapped, not loaded, so the bound is only a sanity limit.
MAX_IMAGE_BYTES = 8 * 1024**3


def release_owned_image(name):
    """Images compiled by this release, including embedded dependency owners.

    This recognizes release-built names; it never exempts an image from the
    scan. An unrecognized image is still scanned and must be clean unless its
    exact bytes are declared third-party. A prebuilt ggml/Nemo/Pocket image is
    still our release build input, not a vendor exception to build-path privacy.
    """
    path = Path(name)
    if path.parent.as_posix() not in ('bin', 'lib', '.'):
        return False
    return bool(re.fullmatch(
        r'(?:lib)?(?:aii(?:i)?_[A-Za-z0-9_]+|ggml(?:-[a-z0-9]+)*|nemo_speech_asr(?:_c)?|native_pocket_resident'
        r'|pocket_tts|webrtc_aec3|sentencepiece)'
        r'(?:\.exe|\.dll|(?:\.\d+)*\.dylib|\.so(?:\.\d+)*)?', path.name)) or path.name in ('aii-voice-t3', 'aii-voice-t3.exe')


def native_image(name, head, executable=False):
    """Content decides, not a name list: any native header, image suffix or executable."""
    return bool(executable) or head.startswith(IMAGE_MAGIC) or bool(IMAGE_NAME.search(name))


def third_party_images(path, digest):
    """Exact-byte declarations of vendor redistributables, bound by their hash."""
    if path is None:
        if digest is not None:
            raise ValueError('third-party image digest supplied without its declaration')
        return {}
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('third-party image declaration changed')
    record = json.loads(raw)
    images = record.get('images') if isinstance(record, dict) else None
    if (set(record) != {'schema', 'images'} or record['schema'] != THIRD_PARTY_SCHEMA
            or not isinstance(images, dict)):
        raise ValueError('third-party image declaration fields differ')
    for name, row in images.items():
        if (not isinstance(row, dict) or set(row) != {'sha256', 'distribution'}
                or not re.fullmatch('[0-9a-f]{64}', str(row['sha256']))
                or not isinstance(row['distribution'], str) or not row['distribution'].strip()):
            raise ValueError('third-party image needs exact sha256 and distribution: ' + name)
    return images


def audit_release_images(runtime, profile, carrier, third_party=None, private_prefixes=()):
    """Scan the complete native image census of a runtime plus its carrier.

    Returns one row per image with its runtime-relative name, digest, role and
    finding counts. Release-owned (that is, every undeclared) image must be
    clean; a declared third-party image may only report generic build roots.
    """
    files, declared = profile['files'], dict(third_party or {})
    for name, row in declared.items():
        if files.get(name, {}).get('sha256') != row['sha256']:
            raise ValueError('third-party declaration does not name shipped bytes: ' + name)
        if release_owned_image(name):
            raise ValueError('release-built image cannot be declared third-party: ' + name)
    rows = []
    for name in sorted(files):
        path = Path(runtime)/name
        with path.open('rb') as f:
            head = f.read(4)
        if not native_image(name, head, files[name].get('executable')):
            continue
        row = audit_image(path, private_prefixes)
        if row['sha256'] != files[name]['sha256']:
            raise ValueError('runtime image differs from its bound inventory: ' + name)
        row.update(file=name, role='third_party' if name in declared else 'release_owned')
        rows.append(row)
    if not any(r['role'] == 'release_owned' for r in rows):
        raise ValueError('release-owned image census is empty')
    row = audit_image(carrier, private_prefixes)
    row.update(file=Path(carrier).name, role='release_owned')
    rows.append(row)
    failed = sorted(Path(r['file']).name for r in rows if any(
        r['role'] == 'release_owned' or key.split(':', 1)[1] in ALWAYS_FAIL for key in r['findings']))
    if failed:
        raise ValueError('release-owned images contain private strings: '+', '.join(failed))
    return rows


def scan_bytes(data, private_prefixes=()):
    findings = {}
    prefixes = [value.replace('\\', '/').casefold() for value in private_prefixes]
    if any(not value for value in prefixes):
        raise ValueError('empty private prefix')
    for encoding, pattern in [('ascii', rb'[\x20-\x7e]{5,}'),
                              ('utf-16-le', rb'(?:[\x20-\x7e]\0){5,}')]:
        for match in re.finditer(pattern, data):
            text = match.group().decode(encoding)
            normalized = text.replace('\\', '/')
            categories = []
            if ROOT.search(normalized) and normalized not in PUBLIC_RUNTIME_PATHS:
                categories.append('private-build-root')
            if any(value in normalized.casefold() for value in prefixes):
                categories.append('explicit-private-prefix')
            categories.extend(category for category in SECRET_CATEGORIES if PATTERNS[category].search(text))
            for category in categories:
                key = encoding+':'+category
                findings[key] = findings.get(key, 0)+1
    return findings


def audit_image(path, private_prefixes=()):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError('regular native image up to 8 GiB required')
    with path.open('rb') as f:
        size = os.fstat(f.fileno()).st_size
        data = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) if size else b''
        try:
            return dict(file=str(path), bytes=size, sha256=hashlib.sha256(data).hexdigest(),
                        findings=scan_bytes(data, private_prefixes))
        finally:
            if size:
                data.close()


def package_metadata(manifest, files, private_prefixes=()):
    """Scan an assembled package's own metadata and records.

    Carrier payloads were scanned as native images at staging. Upstream notice
    texts are preserved byte-for-byte and reviewed separately; JSON records,
    including notice indexes and replacement records, are ours and scanned.
    """
    members = {'manifest.json': json.dumps(manifest, sort_keys=True).encode()}
    members.update((name, raw) for name, raw in files.items() if not name.startswith('payloads/')
                   and not (name.startswith('notices/') and not name.endswith('.json')))
    findings = {name: found for name, raw in sorted(members.items())
                if (found := scan_bytes(raw, private_prefixes))}
    if findings:
        raise ValueError('package metadata contains private strings: ' + ', '.join(findings))
    return dict(members=len(members), findings={})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', type=Path, nargs='+')
    parser.add_argument('--private-prefix', action='append', default=[])
    args = parser.parse_args()
    rows = [audit_image(path, args.private_prefix) for path in args.images]
    passed = not any(row['findings'] for row in rows)
    print(json.dumps(dict(passed=passed, images=rows), indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
