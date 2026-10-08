import subprocess
from pathlib import Path

import pytest

from scripts.check_public_privacy import APPROVED_EMAILS, audit_repository, private_terms, scan_text

# The one approved attribution address, taken from the check so no test spells it.
APPROVED = next(iter(APPROVED_EMAILS))


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
    ('see deliver'+'ables/run-1/result.json', 'unpublished-evidence'),
    ('finding AB'+'C-DE-7 of it', 'unpublished-review'),
    ('an External'+' Review found', 'unpublished-review'),
])
def test_sensitive_text_is_refused_without_echoing_value(text, category):
    rows=scan_text(text)
    assert (1, category) in rows
    assert all(text not in item for _, item in rows)


def test_examples_and_public_attributions_remain_valid():
    assert scan_text('127.0.0.1 /home/user/project /path/to/work/project') == []
    assert scan_text('192.0.2.23 '+APPROVED) == []
    assert scan_text('C:/work/proof/run/coordination.json') == []
    assert scan_text('CC-BY-4.0 and CC-BY-NC-SA-4.0 are licence names') == []
    assert scan_text('Copyright author'+'@'+'example.org', attribution_notice=True) == []
    assert scan_text('h'+'f_'+'x'*32, attribution_notice=True) == [(1,'provider-token')]


def test_assistant_and_noreply_attribution_is_refused():
    # Only the approved address is attribution; the literals are assembled so no such address is spelled in source.
    for address in ('noreply'+'@'+'anthropic'+'.com', 'codex'+'@'+'openai'+'.com', 'maintainer'+'@'+'users.noreply.github.com'):
        assert (1, 'unapproved-email') in scan_text('contact '+address), address
    assert (1, 'attribution-trailer') in scan_text('Co-Authored-'+'By: automated tool')
    assert (2, 'attribution-trailer') in scan_text('subject\n  co-authored-'+'by: someone')


def test_history_finds_removed_sensitive_file(tmp_path):
    def git(*args): return subprocess.run(['git','-C',str(tmp_path),*args],check=True,capture_output=True)
    git('init')
    git('config','user.name','aiii-dot-id')
    git('config','user.email',APPROVED)
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


def test_a_distribution_version_in_a_notice_inventory_is_not_an_address():
    # A package version can have four parts and then reads as an address. In a
    # notice inventory's own distribution member, a package name followed by
    # its version and nothing else, the four parts are the version.
    cudnn = '.'.join(('9', '10', '2', '21')); curand = '.'.join(('10', '4', '1', '81'))
    address = [(1, 'non-example-network-address')]
    for line in ('      "distribution": "nvidia-cudnn-cu12 ' + cudnn + '",',
                 '"distribution": "libcurand-13-1 ' + curand + '"'):
        assert scan_text(line, attribution_notice=True) == [], line
        # Control half: outside a notice the same line names an address.
        assert scan_text(line) == address, line
    # Control halves, in a notice: the four parts are still an address wherever
    # the line is anything but one package and its version.
    assert scan_text('"homepage": "http://' + curand + '/"', attribution_notice=True) == address
    assert scan_text('"distribution": "' + curand + '"', attribution_notice=True) == address
    assert scan_text('"distribution": "mirror ' + curand + ' port 80"', attribution_notice=True) == address
    assert scan_text('"distribution": "mirror ' + curand + ' ' + cudnn + '"', attribution_notice=True) == address * 2
    assert scan_text('"distribution": "nvidia-cudnn-cu12 ' + cudnn + '", "host": "' + curand + '"', attribution_notice=True) == address * 2


def test_listed_terms_are_refused_by_number_and_never_echoed(tmp_path):
    listed = tmp_path / 'terms.txt'
    listed.write_text('# kept outside the tree\nword:Quillon\nsub:corridor-9\nallow:other/file:Quillon\n\nword:aiii\n')
    terms = private_terms(listed)
    assert [n for n, _ in terms] == [1, 2, 3]
    rows = scan_text('ask Quillon\nQuillons and quillon are other words\nhost corridor-91', terms=terms)
    assert rows == [(1, 'private-term-1'), (3, 'private-term-2')]
    assert all('Quillon' not in category and 'corridor' not in category for _, category in rows)
    # The approved address is attribution, not a finding, even when a listed word is part of it.
    assert scan_text('author '+APPROVED, terms=terms) == []
    assert scan_text('ask aiii', terms=terms) == [(1, 'private-term-3')]


def test_listed_terms_are_found_in_history_and_commit_messages(tmp_path):
    def git(*args): return subprocess.run(['git','-C',str(tmp_path),*args],check=True,capture_output=True)
    listed = tmp_path.parent / (tmp_path.name + '-terms.txt')
    listed.write_text('word:Quillon\n')
    terms = private_terms(listed)
    git('init'); git('config','user.name','aiii-dot-id'); git('config','user.email',APPROVED)
    (tmp_path/'note.txt').write_text('ask Quillon')
    git('add','note.txt'); git('commit','-m','add a note')
    git('rm','note.txt'); git('commit','-m','Quillon asked for this')
    assert [row[1:] for row in audit_repository(tmp_path, terms=terms)[0]] == [('<commit-metadata>', 3, 'private-term-1')]
    assert ('note.txt', 1, 'private-term-1') in [row[1:] for row in audit_repository(tmp_path, history=True, terms=terms)[0]]
    assert audit_repository(tmp_path, history=True)[0] == []
