import copy
import json

import pytest

from scripts.prepare_catalog_update import digest, json_bytes, prepare, render_update
from scripts.verify_publication_catalog import check_catalog


def entry():
    return dict(id='id.aiii.voice', version='0.1.0-beta.4', tier='T3', summary='AII Voice',
                aiios_min_version='0.1.8', packages=[dict(platform='macos', arch='arm64',
                url='https://example.test/voice.aiiospkg', sha256='sha256:' + 'a'*64, size=42)])


def manifest():
    return dict(id='id.aiii.voice', version='0.1.0-beta.4', title='AII Voice',
                aiios_min_version='0.1.8', variants=[dict(platform='macos', arch='arm64')])


def check(value, source=None):
    check_catalog(value, source or manifest(), 'a'*64, 42, 'https://example.test/voice.aiiospkg')


@pytest.mark.parametrize('change', ['missing-min', 'older-min', 'empty-min', 'null-min',
                                   'invented-max', 'missing-max', 'wrong-max'])
def test_host_window_cannot_drift_from_signed_manifest(change):
    value, source = entry(), manifest()
    if change == 'missing-min':
        del value['aiios_min_version']
    elif change == 'older-min':
        value['aiios_min_version'] = '0.1.7'
    elif change == 'empty-min':
        value['aiios_min_version'] = ''
    elif change == 'null-min':
        value['aiios_min_version'] = None
    elif change == 'invented-max':
        value['aiios_max_exclusive_version'] = '0.2.0'
    else:
        source['aiios_max_exclusive_version'] = '0.2.0'
        if change == 'wrong-max':
            value['aiios_max_exclusive_version'] = '0.3.0'
    with pytest.raises(ValueError, match='compatibility'):
        check(value, source)


def test_exact_or_omitted_window_passes():
    check(entry())
    value, source = entry(), manifest()
    del value['aiios_min_version']
    del source['aiios_min_version']
    check(value, source)


def old_catalog():
    return dict(catalog_version=1, generated='2026-09-17T19:08:28Z',
                future_field={'keep': ['everything']}, must_understand=['future-feature'],
                plugins=[dict(id='another.plugin', version='3', opaque={'preserve': True}),
                         dict(entry(), version='0.1.0-beta.3')])


def encoded(value):
    return ('Before.\n```json\n' + json.dumps(value) + '\n```\nAfter.\n').encode()


def render(raw, **kwargs):
    args = dict(raw=raw, expected_sha256=digest(raw), entry=entry(),
                details={'description': 'English desktop beta.'}, generated='2026-09-18T20:00:00Z')
    return render_update(**(args | kwargs))


def test_only_voice_and_generation_change_with_compat_requirement():
    original = old_catalog()
    before = copy.deepcopy(original)
    result, payload = render(encoded(original))
    observed = json.loads(result.decode().split('```json\n')[1].split('```')[0])
    assert original == before
    assert observed['plugins'][0] == before['plugins'][0]
    assert observed['future_field'] == before['future_field']
    assert observed['must_understand'] == ['future-feature', 'compat']
    assert observed['plugins'][1] == entry() | {'description': 'English desktop beta.'}
    assert result.startswith(b'Before.\n') and result.endswith(b'After.\n')
    assert payload['catalog_sha256'] == 'sha256:' + digest(result)
    assert payload['generated'] == observed['generated']
    assert render(result)[0] == result


def test_changed_catalog_is_not_overwritten():
    with pytest.raises(ValueError, match='changed'):
        render(encoded(old_catalog()), expected_sha256='0'*64)


@pytest.mark.parametrize('key', ['version', 'id', 'packages', 'aiios_min_version', 'aiios_max_exclusive_version'])
def test_descriptive_details_cannot_override_package_facts(key):
    with pytest.raises(ValueError, match='override'):
        render(encoded(old_catalog()), details={key: 'invented'})


@pytest.mark.parametrize('case', ['duplicate', 'missing', 'two-blocks', 'unterminated'])
def test_ambiguous_catalog_refused(case):
    old = old_catalog()
    if case == 'duplicate':
        old['plugins'].append(entry())
    elif case == 'missing':
        old['plugins'].pop()
    raw = encoded(old)
    if case == 'two-blocks':
        raw += b'```json\n{}\n```\n'
    elif case == 'unterminated':
        raw = raw[:raw.rfind(b'```')]
    with pytest.raises(ValueError):
        render(raw)


@pytest.mark.parametrize('value', ['2026-09-18', '2026-09-18Z', '2026-09-18 20:00:00Z',
                                 '2026-09-18T20:00:00', '2026-99-18T20:00:00Z'])
def test_generation_time_is_unambiguous(value):
    with pytest.raises(ValueError):
        render(encoded(old_catalog()), generated=value)


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    # This fixture tests handoff binding, not cryptography. The release proof
    # separately runs the real host verifier against the real signed archive.
    package = tmp_path / 'fixture.aiiospkg'
    package.write_bytes(b'synthetic archive')
    row = dict(kind='plugin', file=package.name, size=package.stat().st_size,
               sha256=digest(package.read_bytes()), url='https://example.test/voice.aiiospkg')
    value = entry()
    value['packages'][0].update(size=row['size'], sha256='sha256:' + row['sha256'])
    (tmp_path / 'catalog-entry.json').write_bytes(json_bytes(value))
    verdict = dict(passed=True, tier='T3', host_vcs_modified=False,
                   signed_package_sha256=row['sha256'], tampered_archive_rejected=True)
    (tmp_path / 'host-verification.json').write_bytes(json_bytes(verdict))
    plan = dict(assets=[row], signed_package_sha256=row['sha256'],
                catalog_entry_sha256=digest(json_bytes(value)),
                host_verification_sha256=digest(json_bytes(verdict)))
    (tmp_path / 'publication-plan.json').write_bytes(json_bytes(plan))
    raw = encoded(old_catalog())
    catalog = tmp_path / 'catalog.md'
    catalog.write_bytes(raw)
    details = tmp_path / 'details.json'
    details.write_text('{}')
    monkeypatch.setattr('scripts.prepare_catalog_update.read_package',
                        lambda *args, **kwargs: (manifest(), {}))
    return dict(catalog=catalog, expected_sha256=digest(raw), handoff=tmp_path,
                details_file=details, generated='2026-09-18T20:00:00Z', out=tmp_path / 'prepared')


def test_preparation_neither_signs_nor_overwrites(preparation):
    result = prepare(**preparation)
    assert result['prepared'] and not result['signed'] and not result['published']
    out = preparation['out']
    assert not (out / 'aiios-plugins.md.sig').exists()
    assert digest((out / 'aiios-plugins.md').read_bytes()) == result['catalog_sha256']
    with pytest.raises(FileExistsError):
        prepare(**preparation)


@pytest.mark.parametrize('case', ['entry', 'archive', 'receipt', 'package-hash', 'path', 'verdict'])
def test_bad_handoff_never_emits_catalog(preparation, case):
    root = preparation['handoff']
    if case in ('entry', 'archive', 'receipt'):
        target = {'entry': 'catalog-entry.json', 'archive': 'fixture.aiiospkg',
                  'receipt': 'host-verification.json'}[case]
        file = root / target
        file.write_bytes(file.read_bytes() + b' ')
    else:
        plan = json.loads((root / 'publication-plan.json').read_text())
        if case == 'package-hash':
            plan['signed_package_sha256'] = 'b' * 64
        elif case == 'path':
            plan['assets'][0]['file'] = '../fixture.aiiospkg'
        else:
            verdict = json.loads((root / 'host-verification.json').read_text())
            verdict['host_vcs_modified'] = True
            (root / 'host-verification.json').write_bytes(json_bytes(verdict))
            plan['host_verification_sha256'] = digest(json_bytes(verdict))
        (root / 'publication-plan.json').write_bytes(json_bytes(plan))
    with pytest.raises(ValueError):
        prepare(**preparation)
    assert not preparation['out'].exists()
