"""Read-only privacy check of explicitly selected release-owned native images.

Inspect ASCII and UTF-16 strings, including compiler macros, linker paths and
CodeView references. Report categories/counts, never matched private values.
This is not an audit of arbitrary personal names, certificates or model data.
Review vendor notices and all final archive metadata separately.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from scripts.check_public_privacy import PATTERNS

ROOT = re.compile(r'(?i)(?:[/](?:Users|Volumes|home|private[/]tmp)[/]|[/]work[/]|[a-z]:[/](?:Users|work)[/])')
SECRET_CATEGORIES = ('private-key', 'provider-token', 'jwt', 'credential-url')


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
            if ROOT.search(normalized):
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
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 512*1024*1024:
        raise ValueError('regular native image up to 512 MiB required')
    data = path.read_bytes()
    return dict(file=str(path), bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                findings=scan_bytes(data, private_prefixes))


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
