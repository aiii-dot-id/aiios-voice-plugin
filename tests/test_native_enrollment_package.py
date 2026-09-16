"""Exercise our assembler with real carrier emissions and the landed SDK.

Temporary bundles test declaration composition only; they are not installable
checkpoint qualification, and no installed package is changed.
"""
import hashlib
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

from scripts.build_plugin_carrier import ROOT, SDK_SOURCE, verify_build, build

BASE = ROOT / 'deliverables/checkpoints/common-native-macos-signing-20260912-r1'
GO = '/usr/local/go1.27/bin/go'


@pytest.fixture(scope='module')
def carrier_build(tmp_path_factory):
    directory = tmp_path_factory.mktemp('voice-package-carrier') / 'build'
    build(Path(GO), directory)
    verify_build(directory)
    return directory


@pytest.fixture(scope='module')
def assembler(tmp_path_factory, carrier_build):
    out = tmp_path_factory.mktemp('voice-assembler') / 'assemble'
    subprocess.run([GO, 'build', '-trimpath', '-buildvcs=false', '-o', str(out),
                    str(ROOT / 'scripts/private_cp1_package.go')], cwd=SDK_SOURCE,
                   env={**os.environ, 'GOTOOLCHAIN': 'local', 'GOWORK': 'off',
                        'GOPROXY': 'off'}, check=True, capture_output=True, timeout=60)
    return out


def inputs(directory, multi, carrier_build):
    cfg = json.loads((BASE / 'plugin.json').read_text())
    carrier = carrier_build / 'aii-voice-t3' if multi else BASE / 'aii-voice-t3'
    raw = subprocess.check_output([str(carrier)], env={'PATH': '', 'AIISDK_DESCRIBE': '1'}, timeout=10)
    rows = json.loads(raw)
    if multi:
        shutil.copytree(ROOT / 'plugin/native/schemas', directory / 'schemas')
        del cfg['interface']
        speech = [r['id'] for r in rows if r['id'].startswith('speech.session.')]
        speaker = [r['id'] for r in rows if r['id'].startswith('speaker.')]
        assert len(speech) == 8
        assert set(speaker) == {'speaker.enroll', 'speaker.list', 'speaker.remove',
                                'speaker.reset', 'speaker.discard_capture',
                                'speaker.upgrade_policy'}
        cfg['interfaces'] = [{'id': 'speech.session', 'version': 1, 'methods': speech},
                             {'id': 'speaker.uid', 'version': 1, 'methods': speaker}]
    (directory / 'plugin.json').write_text(json.dumps(cfg))
    (directory / 'descriptors.json').write_bytes(raw)
    shutil.copyfile(carrier, directory / 'aii-voice-t3')
    return cfg, rows


def test_single_interface_package_bytes_stay_exact(assembler, tmp_path, carrier_build):
    cfg, _ = inputs(tmp_path, False, carrier_build)
    done = subprocess.run([str(assembler), str(tmp_path)], capture_output=True, check=True, timeout=30)
    name = json.loads(done.stdout)['bundle']
    assert (tmp_path / name).read_bytes() == (BASE / name).read_bytes()


def test_two_interfaces_preserve_all_controls_and_confirmation(assembler, tmp_path, carrier_build):
    cfg, rows = inputs(tmp_path, True, carrier_build)
    done = subprocess.run([str(assembler), str(tmp_path)], capture_output=True, check=True, timeout=30)
    result = json.loads(done.stdout)
    with tarfile.open(tmp_path / result['bundle']) as archive:
        files = {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}
    prefix = cfg['id'] + '-' + cfg['version'] + '/'
    manifest = json.loads(files[prefix + 'manifest.json'])
    seen = set()
    for interface in manifest['interfaces']['core']:
        raw = files[prefix + 'install-root/interfaces/' + interface['id'] + '.v1.schema.json']
        assert interface['schema_hash'] == 'sha256:' + hashlib.sha256(raw).hexdigest()
        subset = json.loads(raw)
        assert [r['id'] for r in subset] == interface['methods']
        for row in subset:
            assert row == next(r for r in rows if r['id'] == row['id'])
            assert row['id'] not in seen
            seen.add(row['id'])
            if interface['id'] == 'speaker.uid':
                assert row['family'] == 'speaker' and row['examples'] and 'UID' in row['keywords']
                assert row.get('operator_confirms', False) == (row['id'] != 'speaker.list')
                for ref in (row['input'],row['output']):
                    assert files[prefix+'install-root/'+ref]==(ROOT/'plugin/native'/ref).read_bytes()
    assert seen == {r['id'] for r in rows} and len(seen) == 14
    assert manifest['variants'][0]['implements']['core'] == ['speech.session@1', 'speaker.uid@1']


def test_checkpoint_packager_preserves_the_full_guided_surface(tmp_path, carrier_build):
    from scripts.package_common_native_checkpoint import enrollment_interfaces
    _, rows = inputs(tmp_path, True, carrier_build)
    interfaces = enrollment_interfaces(rows)
    assert {n for i in interfaces for n in i['methods']} == {r['id'] for r in rows}
    assert len(interfaces[1]['methods']) == 6
    for missing in ('speaker.discard_capture', 'speaker.upgrade_policy'):
        with pytest.raises(ValueError, match='incomplete'):
            enrollment_interfaces([r for r in rows if r['id'] != missing])
    with pytest.raises(ValueError, match='duplicate'):
        enrollment_interfaces(rows + [rows[0]])
    bad = copy.deepcopy(rows)
    next(r for r in bad if r['id'] == 'speaker.upgrade_policy')['operator_confirms'] = False
    with pytest.raises(ValueError, match='confirmation'):
        enrollment_interfaces(bad)


@pytest.mark.parametrize('damage', ['input', 'output', 'summary', 'examples', 'empty_object',
                                  'parameter_description', 'missing_example_key', 'invented_example_key'])
def test_ai_visible_contract_cannot_regress_to_an_empty_offer(assembler, tmp_path, carrier_build, damage):
    _, rows = inputs(tmp_path, True, carrier_build)
    row = next(r for r in rows if r['id'] == 'speaker.enroll')
    if damage in ('input', 'output', 'summary'):
        row[damage] = ''
    elif damage == 'examples':
        row[damage] = []
    elif damage in ('empty_object', 'parameter_description'):
        path = tmp_path / row['input']
        schema = json.loads(path.read_text())
        if damage == 'empty_object':
            schema = {'type': 'object', 'properties': {}, 'additionalProperties': True}
        else:
            del schema['properties']['finals']['description']
        path.write_text(json.dumps(schema))
    else:
        example = json.loads(row['examples'][0])
        if damage == 'missing_example_key': del example['speaker_id']
        else: example['audio_path'] = '/not-a-supported-argument'
        row['examples'] = [json.dumps(example)]
    (tmp_path / 'descriptors.json').write_text(json.dumps(rows))
    run = subprocess.run([str(assembler), str(tmp_path)], capture_output=True, timeout=30)
    assert run.returncode != 0 and not list(tmp_path.glob('*.aiiospkg'))
    assert b'speaker' in run.stderr


@pytest.mark.parametrize('damage',['missing','outside','symlink','keyword'])
def test_declared_schema_is_never_silently_dropped(assembler,tmp_path,carrier_build,damage):
    _,rows=inputs(tmp_path,True,carrier_build)
    row=next(r for r in rows if r['id']=='speaker.enroll')
    path=tmp_path/row['input']
    if damage=='missing':path.rename(path.with_suffix('.absent'))
    elif damage=='outside':row['input']='../outside.json'
    elif damage=='symlink':
        path.rename(path.with_suffix('.saved'))
        outside=tmp_path.parent/(tmp_path.name+'-outside.json');outside.write_text('{}')
        path.symlink_to(outside)
    else:path.write_text('{"type":"object","unsupportedConstraint":true}')
    (tmp_path/'descriptors.json').write_text(json.dumps(rows))
    run=subprocess.run([str(assembler),str(tmp_path)],capture_output=True,timeout=30)
    assert run.returncode!=0 and not list(tmp_path.glob('*.aiiospkg'))


@pytest.mark.parametrize('platform', ['linux', 'windows'])
def test_native_x86_metadata_is_accepted_by_real_sdk_assembler(assembler, tmp_path, platform, carrier_build):
    # Tests portable declarations, not execution of this Mac fixture payload
    # on another OS. The desktop real-engine gates supply that other evidence.
    from scripts.package_common_native_checkpoint import platform_spec
    cfg, _ = inputs(tmp_path, True, carrier_build)
    s = platform_spec({'platform': platform, 'arch': 'amd64'})
    v = cfg['variants'][0]
    v.update(variant_id=s['variant'], platform=s['platform'], arch=s['arch'])
    v['accelerator'].update(os=s['platform'], arch=s['arch'], backend=s['backend'])
    cfg['runtimes'][0]['variant_id'] = s['variant']
    (tmp_path / 'plugin.json').write_text(json.dumps(cfg))
    done = subprocess.run([str(assembler), str(tmp_path)], capture_output=True, check=True, timeout=30)
    bundle = json.loads(done.stdout)
    assert bundle['unsigned'] and bundle['canonical_reassembly_equal']


@pytest.mark.parametrize('damage', ['orphan', 'duplicate', 'unknown'])
def test_partition_damage_never_produces_a_bundle(assembler, tmp_path, damage, carrier_build):
    cfg, _ = inputs(tmp_path, True, carrier_build)
    methods = cfg['interfaces'][1]['methods']
    if damage == 'orphan':
        methods.pop()
    elif damage == 'duplicate':
        methods.append(cfg['interfaces'][0]['methods'][0])
    else:
        methods.append('speaker.invented')
    (tmp_path / 'plugin.json').write_text(json.dumps(cfg))
    done = subprocess.run([str(assembler), str(tmp_path)], capture_output=True, timeout=30)
    assert done.returncode != 0
    assert not list(tmp_path.glob('*.aiiospkg'))


@pytest.mark.parametrize('missing', [False, True])
def test_family_contains_each_explicit_platform_artifact_or_refuses(assembler, tmp_path, missing, carrier_build):
    # Payload sentinels test packaging, not native execution or qualification.
    from scripts.package_common_native_checkpoint import platform_spec
    cfg, _ = inputs(tmp_path, True, carrier_build)
    template, runtime = cfg['variants'][0], cfg['runtimes'][0]
    cfg['variants'], cfg['runtimes'] = [], []
    expected = {}
    for platform, arch in [('macos', 'arm64'), ('linux', 'amd64'), ('windows', 'amd64')]:
        spec = platform_spec({'platform': 'darwin' if platform == 'macos' else platform, 'arch': arch})
        variant = copy.deepcopy(template)
        variant.update(variant_id=spec['variant'], platform=spec['platform'], arch=spec['arch'],
                       artifact='payloads/' + platform)
        variant['accelerator'].update(os=spec['platform'], arch=spec['arch'], backend=spec['backend'])
        cfg['variants'].append(variant)
        companion = copy.deepcopy(runtime)
        companion['variant_id'] = spec['variant']
        cfg['runtimes'].append(companion)
        payload = tmp_path / variant['artifact']
        payload.parent.mkdir(exist_ok=True)
        payload.write_bytes(('distinct native test sentinel: ' + platform).encode())
        expected[spec['variant']] = payload.read_bytes()
    if missing:
        del cfg['variants'][1]['artifact']
    (tmp_path / 'plugin.json').write_text(json.dumps(cfg))
    done = subprocess.run([str(assembler), str(tmp_path)], capture_output=True, timeout=30)
    if missing:
        assert done.returncode != 0 and b'each family variant requires' in done.stderr
        assert not list(tmp_path.glob('*.aiiospkg'))
        return
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert set(report['runtime_ceilings_by_variant']) == set(expected)
    with tarfile.open(tmp_path / report['bundle']) as archive:
        files = {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}
    prefix = cfg['id'] + '-' + cfg['version'] + '/'
    manifest = json.loads(files[prefix + 'manifest.json'])
    assert len(manifest['variants']) == 3
    for variant in manifest['variants']:
        name = variant.get('variant_id', variant.get('id'))
        suffix = '.exe' if name.startswith('windows-') else ''
        assert files[prefix + 'install-root/variants/' + name + '/plugin' + suffix] == expected[name]


@pytest.mark.parametrize('escape', ['parent', 'symlink'])
def test_explicit_artifact_cannot_leave_assembly_root(assembler, tmp_path, carrier_build, escape):
    cfg, _ = inputs(tmp_path, True, carrier_build)
    outside = tmp_path.parent / (tmp_path.name + '-outside.bin')
    outside.write_bytes(b'outside fixture, never a package artifact')
    if escape == 'parent':
        artifact = '../' + outside.name
    else:
        artifact = 'outside-link'
        (tmp_path / artifact).symlink_to(outside)
    cfg['variants'][0]['artifact'] = artifact
    (tmp_path / 'plugin.json').write_text(json.dumps(cfg))
    done = subprocess.run([str(assembler), str(tmp_path)], capture_output=True, timeout=30)
    assert done.returncode != 0
    assert b'outside fixture' not in done.stdout + done.stderr
    assert not list(tmp_path.glob('*.aiiospkg'))
