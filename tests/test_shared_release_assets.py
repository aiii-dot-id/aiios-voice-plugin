"""Shared model declarations must close through one exact publication asset."""
import copy
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from scripts import stage_desktop_publication as publication
from scripts import stage_beta1_signed_publication as signed_publication


def asset():
    return dict(kind='model', file='assets/model.bin', size=7, sha256='a' * 64,
                url='https://github.com/example/model.bin', source='first')


def test_identical_bindings_share_an_asset_without_mutating_inputs():
    first = asset()
    second = dict(first, source='second')
    rows = [first, second]
    before = copy.deepcopy(rows)
    assert publication.release_assets(rows) == [first]
    assert rows == before


@pytest.mark.parametrize('field,value', [('kind', 'runtime'), ('size', 8),
    ('sha256', 'b' * 64), ('url', 'https://github.com/example/other.bin')])
def test_same_filename_with_conflicting_binding_is_refused(field, value):
    with pytest.raises(ValueError, match='colliding'):
        publication.release_assets([asset(), dict(asset(), **{field: value})])


def shared_candidate(tmp_path, monkeypatch, runtime_data=b'runtime', runtime_filename='runtime.tar.gz'):
    """Two models share one release asset; the runtime may be made to collide with it."""
    candidate = tmp_path / 'candidate'
    generated = tmp_path / 'generated'
    (candidate / 'author').mkdir(parents=True)
    (candidate / 'assets').mkdir()
    generated.mkdir()
    package = candidate / 'author' / 'voice.aiiospkg'
    package.write_bytes(b'package')
    version = '0.1.0-beta.7'
    base = 'https://github.com/' + publication.REPOSITORY + '/releases/download/v' + version + '/'

    def dependency(data, filename):
        digest = hashlib.sha256(data).hexdigest()
        name = digest + '-' + filename
        return dict(sha256=digest, size=len(data), url=base + name), name

    model, model_file = dependency(b'model', 'model.bin')
    runtime, runtime_file = dependency(runtime_data, runtime_filename)
    (generated / model_file).write_bytes(b'model')
    (candidate / 'assets' / runtime_file).write_bytes(runtime_data)
    models = [dict(model, name=n, path=n + '/model.bin') for n in ('one', 'two')]
    variants = [dict(variant_id=p, platform=p, artifact_hash='sha256:' + 'b' * 64)
                for p in ('macos', 'linux', 'windows')]
    runtimes = [dict(runtime, variant_id=v['variant_id'], installed_bytes=12) for v in variants]
    profiles = {v['variant_id']: dict(models=['one', 'two']) for v in variants}
    manifest = dict(version=version, variants=variants)
    index = dict(distribution_review_complete=False, open_items=['Unfinished review'],
        models=[dict(path=m['path'], sha256=m['sha256'], bytes=m['size'], notice_group='models') for m in models])
    payloads = {name: json.dumps(data).encode() for name, data in (
        ('models.json', models), ('runtime.json', dict(runtimes=runtimes)),
        ('accelerator.json', profiles), ('notices/INDEX.json', index))}
    payloads['notices/models/NOTICE'] = b'Original upstream notice'
    monkeypatch.setattr(publication, 'read_package', lambda path, digest: (manifest, payloads))
    package_hash = publication.sha(package)
    (candidate / 'result.json').write_text(json.dumps(dict(passed=True, signed=False,
        beta_release_ready=False, bundle=dict(bundle=package.name, sha256=package_hash))))
    setup = dict(automatic_configuration=False, platforms={p: {} for p in profiles})
    (candidate / 'operator-setup.json').write_text(json.dumps(setup))
    upstream = tmp_path / 'upstream.json'
    upstream.write_text(json.dumps(dict(passed=True, rows=[], utc='2026-01-01T00:00:00Z')))
    return SimpleNamespace(candidate=candidate, generated=generated, upstream=upstream,
                           out=tmp_path / 'out', manifest=manifest, payloads=payloads,
                           models=models, profiles=profiles, index=index, setup=setup)


def stage_signed(tmp_path, monkeypatch, fixture):
    signed_package = tmp_path / 'voice.aiiospkg'
    signed_package.write_bytes(b'signed package fixture')
    verdict = tmp_path / 'verification.json'
    verdict.write_text(json.dumps(dict(passed=True, tier='T3', signed_package_sha256=publication.sha(signed_package),
        tampered_archive_rejected=True, host_vcs_modified=False)))
    reused = tmp_path / 'reuse.json'
    reused.write_text(json.dumps(dict(passed=True, method='anonymous_github_release_asset_digest', rows=[])))
    sdk = tmp_path / 'sdk'
    sdk.write_bytes(b'publisher fixture')
    monkeypatch.setattr(signed_publication, 'read_package', lambda *args, **kwargs: (fixture.manifest, fixture.payloads))
    monkeypatch.setattr(signed_publication.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=b'{}', stderr=b''))
    monkeypatch.setattr(signed_publication, 'check_catalog', lambda *args: None)
    monkeypatch.setattr(sys, 'argv', ['stage-signed', '--candidate', str(fixture.candidate), '--signed', str(signed_package),
        '--host-verification', str(verdict), '--generated', str(fixture.generated), '--upstream', str(fixture.upstream),
        '--reused', str(reused), '--out', str(fixture.out), '--sdk-tool', str(sdk)])
    signed_publication.main()
    return signed_package


@pytest.mark.parametrize('signed', [False, True])
def test_stage_and_readback_close_shared_model_and_runtime_assets(tmp_path, monkeypatch, signed):
    fixture = shared_candidate(tmp_path, monkeypatch)
    out = fixture.out
    if signed:
        signed_package = stage_signed(tmp_path, monkeypatch, fixture)
        plan = json.loads((out / 'publication-plan.json').read_text())
        assert len(plan['assets']) == 3
        assert {r['kind'] for r in plan['assets']} == {'model', 'runtime', 'plugin'}
        assert plan['signed_package_sha256'] == publication.sha(signed_package)
        assert plan['signed'] and not plan['published'] and not plan['installed']
        assert len(plan['variants']) == 3
        return

    result = publication.stage(fixture.candidate, fixture.generated, fixture.upstream, out)
    plan = json.loads((out / 'publication-plan.json').read_text())
    assert result['passed'] and result['assets'] == 3
    assert {r['kind'] for r in plan['assets']} == {'model', 'runtime', 'plugin'}
    assert len(plan['variants']) == 3
    assert all(len(v['models']) == 2 for v in plan['variants'].values())
    assert plan['distribution_review_complete'] is False
    assert plan['beta_release_ready'] is False
    assert publication.audit_staged(out)['passed']


@pytest.mark.parametrize('signed', [False, True])
def test_model_and_runtime_sharing_a_release_name_are_refused_before_output(tmp_path, monkeypatch, signed):
    # Identical bytes under one release URL, declared once as a model and once
    # as a runtime: deduplication must not silently pick either declaration.
    fixture = shared_candidate(tmp_path, monkeypatch, runtime_data=b'model', runtime_filename='model.bin')
    with pytest.raises(ValueError, match='colliding'):
        if signed:
            stage_signed(tmp_path, monkeypatch, fixture)
        else:
            publication.stage(fixture.candidate, fixture.generated, fixture.upstream, fixture.out)
    assert not fixture.out.exists()


def test_readback_refuses_conflicting_shared_declaration_even_when_plan_agrees(tmp_path, monkeypatch):
    fixture = shared_candidate(tmp_path, monkeypatch)
    publication.stage(fixture.candidate, fixture.generated, fixture.upstream, fixture.out)
    # The handoff's package now names the same release file twice with
    # different sizes, listed first so that a last-declaration-wins merge
    # would agree with the staged bytes. The plan is regenerated from that
    # package so only the collision itself can refuse it.
    first = fixture.models[0]
    conflicting = dict(first, name='zero', path='zero/model.bin', size=first['size'] + 1)
    models = [conflicting, *fixture.models]
    profiles = {k: dict(models=['zero', 'one', 'two']) for k in fixture.profiles}
    index = copy.deepcopy(fixture.index)
    index['models'].append(dict(path=conflicting['path'], sha256=conflicting['sha256'],
                                bytes=conflicting['size'], notice_group='models'))
    fixture.payloads.update({'models.json': json.dumps(models).encode(),
                             'accelerator.json': json.dumps(profiles).encode(),
                             'notices/INDEX.json': json.dumps(index).encode()})
    plan_path = fixture.out / 'publication-plan.json'
    plan = json.loads(plan_path.read_text())
    targets = publication.dependencies(fixture.manifest, fixture.payloads)[2]
    plan['variants'] = publication.platform_plan(targets, fixture.setup)
    plan_path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match='colliding'):
        publication.audit_staged(fixture.out)
