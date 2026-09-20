"""Fail public-source validation on sensitive paths, credentials or metadata.

Read Git objects, not ignored/untracked local evidence. History mode checks HEAD
and all release tags. Findings name locations/categories, never matched values.
This is a regression check, not a guarantee that arbitrary personal information
or encoded secrets can be detected automatically. Review names and final release
binary strings separately. Upstream license/attribution notices are preserved.
"""
import argparse
import ipaddress
from pathlib import Path
import re
import subprocess


PATTERNS = {
    'private-key': re.compile(r'-----BEGIN (?:ENCRYPTED |RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----'),
    'provider-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|hf_[A-Za-z0-9]{24,}|sk-(?:proj-)?[A-Za-z0-9_-]{30,}|xox[baprs]-[A-Za-z0-9-]{16,}|AKIA[0-9A-Z]{16})\b'),
    'jwt': re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b'),
    'credential-url': re.compile(r'\b(?:https?|ssh|ftp)://[^\s/@:]+:[^\s/@]+@'),
    'literal-credential': re.compile(r'''(?ix)\b(?:password|passwd|api_key|apikey|access_token|refresh_token|client_secret)\b["']?\s*[:=]\s*["'][^"'\r\n]{6,}["']'''),
    'private-root': re.compile(r'(?i)(?<![\w/.-])[/](?:Volumes|Users|private[/]tmp)[/]|(?<![\w/.-])[/]work[/](?!proof/)[^\s/]+|(?<![\w/.-])[/]home[/](?!user(?:/|\b))[^\s/]+|\b[A-Z]:[\\]+Users[\\]+'),
    'private-host': re.compile(r'(?i)\bdev[0-9]+\b'),
}
EMAIL = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')
IPV4 = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
DOCUMENTATION_NETS = tuple(ipaddress.ip_network(n) for n in
                           ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24'))
PRIVATE_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.sqlite', '.db', '.wav', '.mp3',
                    '.m4a', '.npy', '.npz', '.mobileprovision'}


def public_email(value):
    # Existing public tool co-author credits are not operator contact details.
    return value in {'noreply@anthropic.com', 'codex@openai.com'} or bool(
        re.fullmatch(r'[A-Za-z0-9._+\-]+@users\.noreply\.github\.com', value))


def scan_text(text, *, attribution_notice=False):
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        for category, pattern in PATTERNS.items():
            if pattern.search(line): findings.append((number, category))
        if not attribution_notice and any(not public_email(m.group()) for m in EMAIL.finditer(line)):
            findings.append((number, 'unapproved-email'))
        for match in IPV4.finditer(line):
            try: address = ipaddress.ip_address(match.group())
            except ValueError: continue
            if not address.is_loopback and not any(address in net for net in DOCUMENTATION_NETS):
                findings.append((number, 'non-example-network-address'))
    return findings


def is_notice(path):
    # Attribution emails are legitimate only in explicitly named notice files.
    return Path(path).name in {'LICENSE', 'LICENSE.md', 'LICENSE.txt', 'NOTICE', 'NOTICE.txt'}


def audit_repository(root, *, history=False):
    def git(*args): return subprocess.check_output(['git', '-C', str(root), *args])
    commits = git('rev-list', 'HEAD', *(['--tags'] if history else ['--max-count=1'])).decode().splitlines()
    findings, seen, paths = [], set(), set()
    for commit in commits:
        metadata = git('show', '-s', '--format=%ae%n%ce%n%B', commit).decode()
        for line, category in scan_text(metadata):
            findings.append((commit, '<commit-metadata>', line, category))
        for row in git('ls-tree', '-rz', commit).split(b'\0'):
            if not row: continue
            meta, rawpath = row.split(b'\t', 1)
            mode, kind, oid = meta.decode().split()
            path = rawpath.decode(); key=(path, oid); paths.add(path)
            if key in seen: continue
            seen.add(key)
            if kind != 'blob' or mode not in {'100644', '100755'}:
                findings.append((commit, path, 0, 'non-regular-source-object')); continue
            if Path(path).suffix.lower() in PRIVATE_SUFFIXES or Path(path).name.startswith('.env'):
                findings.append((commit, path, 0, 'private-data-file'))
            data=git('cat-file', 'blob', oid)
            if b'\0' in data:
                findings.append((commit, path, 0, 'binary-source-payload'))
            for line, category in scan_text(data.decode('utf-8', errors='replace'), attribution_notice=is_notice(path)):
                findings.append((commit, path, line, category))
    return findings, dict(commits=len(commits), paths=len(paths), file_versions=len(seen))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--history', action='store_true')
    args=parser.parse_args()
    findings, counts=audit_repository(args.repo, history=args.history)
    for commit, path, line, category in findings:
        print(f'{commit[:12]} {path}:{line}: {category}')
    print(f'Privacy check: {counts}; findings={len(findings)}. Manual review is still required.')
    if findings: raise SystemExit(1)


if __name__ == '__main__':
    main()
