import subprocess
from pathlib import Path

import pytest

from scripts.check_public_privacy import audit_repository, scan_text


def test_privacy_guard_and_its_fixtures_do_not_match_themselves():
    root = Path(__file__).resolve().parents[1]
    for relative in ('scripts/check_public_privacy.py', 'tests/test_public_privacy.py'):
        assert scan_text((root / relative).read_text()) == []


@pytest.mark.parametrize('text,category', [
    ('/'+'Users'+'/build-person/project', 'private-root'),
    ('/'+'work'+'/build-person/project', 'private-root'),
    ('host '+'dev'+str(99), 'private-host'),
    ('.'.join(('10','2','3','4')), 'non-example-network-address'),
    ('person'+'@'+'example.com', 'unapproved-email'),
    ('h'+'f_'+'x'*32, 'provider-token'),
    ('-----BEGIN '+'PRIVATE KEY-----', 'private-key'),
    ('https'+':'+'//'+'user'+':'+'password'+'@'+'example.com', 'credential-url'),
    ('api_'+'key="'+'x'*30+'"', 'literal-credential'),
])
def test_sensitive_text_is_refused_without_echoing_value(text, category):
    rows=scan_text(text)
    assert (1, category) in rows
    assert all(text not in item for _, item in rows)


def test_examples_and_public_attributions_remain_valid():
    assert scan_text('127.0.0.1 /home/user/project /path/to/work/project') == []
    assert scan_text('192.0.2.23 maintainer@users.noreply.github.com') == []
    assert scan_text('C:/work/proof/run/coordination.json') == []
    assert scan_text('Co-Authored-By: automated tool <noreply@anthropic.com>') == []
    assert scan_text('Copyright author'+'@'+'example.org', attribution_notice=True) == []
    assert scan_text('h'+'f_'+'x'*32, attribution_notice=True) == [(1,'provider-token')]


def test_history_finds_removed_sensitive_file(tmp_path):
    def git(*args): return subprocess.run(['git','-C',str(tmp_path),*args],check=True,capture_output=True)
    git('init')
    git('config','user.name','Test Maintainer')
    git('config','user.email','test@users.noreply.github.com')
    (tmp_path/'note.txt').write_text('/'+'Users'+'/build-person/project')
    git('add','note.txt'); git('commit','-m','add fixture')
    git('rm','note.txt'); git('commit','-m','remove fixture')
    assert not audit_repository(tmp_path)[0]
    assert any(row[-1]=='private-root' for row in audit_repository(tmp_path,history=True)[0])


def test_commit_identity_is_checked(tmp_path):
    def git(*args): return subprocess.run(['git','-C',str(tmp_path),*args],check=True,capture_output=True)
    git('init'); git('config','user.name','Test Maintainer')
    git('config','user.email','person'+'@'+'example.com')
    git('commit','--allow-empty','-m','fixture')
    assert any(row[1]=='<commit-metadata>' and row[-1]=='unapproved-email'
               for row in audit_repository(tmp_path)[0])
