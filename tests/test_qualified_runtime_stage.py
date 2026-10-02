import hashlib
import io
import json
import tarfile

import pytest
from scripts.stage_qualified_runtime import audit_binding, audit_checkpoint, check_archive, sha, windows_signatures, composition_coordinates, runtime_pack_limits
from scripts.windows_signing_targets import CORE_IMAGES


def test_runtime_budget_is_explicit_and_measures_the_real_dependency_closure():
    rows = {'bin/worker': dict(bytes=100), 'lib/large.so': dict(bytes=2500000000)}
    assert runtime_pack_limits(rows, 3000000000) == dict(installed_bytes=2500000100,
        files=2, file_bytes=2500000000, depth=3, compressed_bytes=3000000000)


def test_coreml_cache_depth_is_not_the_historical_fixture_ceiling():
    rows = {'resources/uid.mlmodelc/' + '/'.join(['cache'] * 9) + '/model.bin': dict(bytes=100)}
    assert runtime_pack_limits(rows, 200)['depth'] == 13


def test_empty_coreml_cache_members_stay_in_the_sealed_inventory():
    limits = runtime_pack_limits({'bin/worker': dict(bytes=100),
                                 'resources/uid.mlmodelc/empty': dict(bytes=0)}, 200)
    assert limits['files'] == 2 and limits['installed_bytes'] == 100


@pytest.mark.parametrize('budget', [None, True, '512M', 0, -1])
def test_missing_or_invalid_archive_budget_cannot_be_guessed(budget):
    with pytest.raises(ValueError, match='compressed archive budget'):
        runtime_pack_limits({'bin/worker': dict(bytes=100)}, budget)


@pytest.mark.parametrize('rows', [{}, {'bin/worker': dict(bytes=0)}, {'bin/worker': dict(bytes=True)}])
def test_runtime_budget_does_not_admit_an_incomplete_inventory(rows):
    with pytest.raises(ValueError, match='runtime inventory'):
        runtime_pack_limits(rows, 100)


@pytest.mark.parametrize('platform,arch,expected', [('darwin','arm64',('macos','arm64')),
    ('linux','amd64',('linux','x86_64')), ('windows','amd64',('windows','x86_64'))])
def test_component_sets_have_explicit_ids_without_relabelling_the_runtime(platform, arch, expected):
    profile = dict(platform=platform, arch=arch)
    assert composition_coordinates(profile) == (*expected, expected[0]+'-'+expected[1]+'-native')
    assert composition_coordinates(profile, 'small-fixture') == (*expected, 'small-fixture')


@pytest.mark.parametrize('variant', ['', '../escape', 'two words', 'A-variant', True])
def test_component_set_name_cannot_escape_or_be_guessed(variant):
    with pytest.raises(ValueError):
        composition_coordinates(dict(platform='darwin',arch='arm64'), variant)


def fixture(path, damage=None):
    pcm=b'native-binary'; digest=hashlib.sha256(pcm).hexdigest()
    rows={'bin/worker':dict(bytes=len(pcm),sha256=digest,executable=True)}
    raw=json.dumps({'files':[dict(path='bin/worker',size=len(pcm),sha256='sha256:'+digest,mode='exec')]}).encode()
    with tarfile.open(path,'w:gz') as t:
        for n in ('runtime/','runtime/bin/'):
            i=tarfile.TarInfo(n);i.type=tarfile.DIRTYPE;i.mode=0o755;t.addfile(i)
        for n,data,mode in (('runtime/inventory.json',raw,0o644),('runtime/bin/worker',pcm,0o755)):
            i=tarfile.TarInfo(n);i.size=len(data);i.mode=mode
            if damage=='mode' and n.endswith('worker'):i.mode=0o644
            if damage=='bytes' and n.endswith('worker'):data=b'X'+data[1:]
            t.addfile(i,io.BytesIO(data))
            if damage=='duplicate' and n.endswith('worker'):t.addfile(i,io.BytesIO(data))
        if damage in ('symlink','extra','traversal'):
            i=tarfile.TarInfo('runtime/extra' if damage!='traversal' else '../escape')
            if damage=='symlink':i.type=tarfile.SYMTYPE;i.linkname='/etc/passwd'
            t.addfile(i,io.BytesIO(b''))
    declaration=dict(sha256=sha(path),size=path.stat().st_size,inventory_sha256=hashlib.sha256(raw).hexdigest(),files=1,installed_bytes=len(pcm))
    if damage=='budget':declaration['installed_bytes']=0
    return declaration,rows


def test_sdk_directory_entries_and_exact_files_are_accepted(tmp_path):
    path=tmp_path/'runtime.tar.gz';declaration,rows=fixture(path)
    check_archive(path,declaration,rows)


def test_release_cannot_strip_required_notice_from_runtime(tmp_path):
    path=tmp_path/'runtime.tar.gz';declaration,rows=fixture(path)
    notice=b'Required component attribution\n'
    rows['resources/VOICE-SOURCE-LICENSES.md']=dict(bytes=len(notice),
        sha256=hashlib.sha256(notice).hexdigest(),executable=False)
    # A self-consistent archive and inventory still cannot omit a file in the
    # carrier's sealed profile. The previous private packaging path did this.
    with pytest.raises(ValueError):check_archive(path,declaration,rows)


def test_release_cannot_downgrade_shared_library_mode(tmp_path):
    path=tmp_path/'runtime.tar.gz'
    content=b'shared-library';digest=hashlib.sha256(content).hexdigest()
    name='lib/libvoice.dylib'
    raw=json.dumps({'files':[dict(path=name,size=len(content),sha256='sha256:'+digest,mode='file')]}).encode()
    with tarfile.open(path,'w:gz') as archive:
        for n,data in [('runtime/inventory.json',raw),('runtime/'+name,content)]:
            info=tarfile.TarInfo(n);info.size=len(data);info.mode=0o644;archive.addfile(info,io.BytesIO(data))
    declaration=dict(sha256=sha(path),size=path.stat().st_size,inventory_sha256=hashlib.sha256(raw).hexdigest(),files=1,installed_bytes=len(content))
    rows={name:dict(bytes=len(content),sha256=digest,executable=True)}
    with pytest.raises(ValueError):check_archive(path,declaration,rows)


@pytest.mark.parametrize('damage',['mode','bytes','duplicate','symlink','extra','traversal','budget'])
def test_bad_archive_refused_even_with_recomputed_archive_digest(tmp_path,damage):
    path=tmp_path/'runtime.tar.gz';declaration,rows=fixture(path,damage)
    with pytest.raises(ValueError):check_archive(path,declaration,rows)


def test_evidence_must_name_exact_runtime(tmp_path):
    p=tmp_path/'audit.json';p.write_text(json.dumps(dict(passed=True,runtime_manifest_sha256='one')))
    audit_binding(p,sha(p),'one')
    with pytest.raises(ValueError):audit_binding(p,sha(p),'two')
    with pytest.raises(ValueError):audit_binding(p,'wrong','one')


def bound_checkpoint(root):
    prefix='C:\\qualification\\signed-checkpoint'
    bound={}
    for name in ('freeze.json','carrier-build.json','runtime/voice-runtime.json','runtime/aii-voice-t3.exe'):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(name.encode())
        bound[prefix+'\\'+name.replace('/','\\')]=sha(path)
    return dict(checkpoint=prefix,bindings=bound)


def test_copied_windows_proof_binds_exact_checkpoint(tmp_path):
    proof=bound_checkpoint(tmp_path)
    audit_checkpoint(proof,tmp_path,'aii-voice-t3.exe')


@pytest.mark.parametrize('name',['freeze.json','carrier-build.json','runtime/voice-runtime.json','runtime/aii-voice-t3.exe'])
def test_modified_checkpoint_or_carrier_is_not_qualified(tmp_path,name):
    proof=bound_checkpoint(tmp_path);(tmp_path/name).write_bytes(b'changed')
    with pytest.raises(ValueError,match='binding differs'):audit_checkpoint(proof,tmp_path,'aii-voice-t3.exe')


def test_evidence_from_another_checkpoint_cannot_match_by_basename(tmp_path):
    proof=bound_checkpoint(tmp_path);proof['checkpoint']='C:\\other'
    with pytest.raises(ValueError,match='binding differs'):audit_checkpoint(proof,tmp_path,'aii-voice-t3.exe')


def test_windows_cannot_skip_native_signature_verification(tmp_path):
    with pytest.raises(ValueError,match='native Authenticode'):
        windows_signatures(tmp_path,dict(platform='windows'),None)


def test_non_windows_cannot_claim_authenticode(tmp_path):
    assert windows_signatures(tmp_path,dict(platform='darwin'),None)==[]
    with pytest.raises(ValueError,match='non-Windows'):
        windows_signatures(tmp_path,dict(platform='darwin'),tmp_path/'signtool')


def test_windows_checks_every_owned_image_and_carrier(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from scripts import stage_qualified_runtime as stage
    monkeypatch.setattr(stage,'os',SimpleNamespace(name='nt'))
    calls=[]
    def verify(root,names,tool):
        calls.append((root,names,tool));return ['observed']
    monkeypatch.setattr(stage,'verify_authenticode',verify)
    # Release-built images beyond the core set are ours to sign; vendor
    # images keep their own signatures and are not ours to verify.
    built=('bin/ggml-vulkan.dll','bin/nemo_speech_asr.dll','bin/webrtc_aec3.dll')
    profile=dict(platform='windows',files=dict.fromkeys([*CORE_IMAGES,*built,'bin/onnxruntime.dll','bin/vulkan-1.dll',
                                                         'bin/msvcp140.dll','resources/settings.json']))
    assert stage.windows_signatures(tmp_path,profile,tmp_path/'signtool')==['observed']
    assert calls==[(tmp_path,CORE_IMAGES|set(built)|{'aii-voice-t3.exe'},tmp_path/'signtool')]
    def refuse(*args):raise ValueError('OS rejected signature')
    monkeypatch.setattr(stage,'verify_authenticode',refuse)
    with pytest.raises(ValueError,match='OS rejected'):stage.windows_signatures(tmp_path,profile,tmp_path/'signtool')
    del profile['files']['bin/aii_voice_worker.exe']
    with pytest.raises(ValueError,match='inventory incomplete'):stage.windows_signatures(tmp_path,profile,tmp_path/'signtool')


def windows_profile():
    from scripts import stage_qualified_runtime as stage
    files = dict.fromkeys(CORE_IMAGES, {})
    files.update({'bin/nemo_speech_asr.dll': {}, 'bin/ggml-vulkan.dll': {}, 'bin/onnxruntime.dll': {},
                  'resources/settings.json': {}})
    return dict(platform='windows', files=files)


def observed(root, names):
    from scripts import stage_qualified_runtime as stage
    from pathlib import Path
    return [dict(path=str(Path(root)/n), status='Valid', subject=stage.SUBJECT, timestamp='TSA') for n in names]


def test_authenticode_is_reported_per_image_not_extended_to_the_runtime(tmp_path):
    from scripts import stage_qualified_runtime as stage
    profile = windows_profile()
    # Observations of only the core set leave other release-built images unverified.
    status =stage.authenticode_status(tmp_path, profile, observed(tmp_path, [*CORE_IMAGES, 'aii-voice-t3.exe']))
    assert status['authenticode_verified'] is False
    assert status['authenticode_unverified_release_images'] == ['bin/ggml-vulkan.dll', 'bin/nemo_speech_asr.dll']
    images = status['authenticode_images']
    assert images['bin/onnxruntime.dll'] == 'not_verified'  # vendor signature is not claimed
    assert all(images[n] == 'verified_publisher' for n in [*CORE_IMAGES, 'aii-voice-t3.exe'])
    assert 'resources/settings.json' not in images
    every = [*CORE_IMAGES, 'aii-voice-t3.exe', 'bin/nemo_speech_asr.dll', 'bin/ggml-vulkan.dll']
    assert stage.authenticode_status(tmp_path, profile, observed(tmp_path, every))['authenticode_verified'] is True


@pytest.mark.parametrize('fault', ['unbound-path', 'invalid', 'other-publisher', 'no-timestamp'])
def test_authenticode_observations_must_bind_valid_shipped_images(tmp_path, fault):
    from scripts import stage_qualified_runtime as stage
    rows = observed(tmp_path, [*CORE_IMAGES, 'aii-voice-t3.exe'])
    if fault == 'unbound-path': rows[0]['path'] = str(tmp_path/'elsewhere.dll')
    if fault == 'invalid': rows[0]['status'] = 'HashMismatch'
    if fault == 'other-publisher': rows[0]['subject'] = 'CN=Other'
    if fault == 'no-timestamp': rows[0]['timestamp'] = None
    with pytest.raises(ValueError, match='Authenticode observation'):
        stage.authenticode_status(tmp_path, windows_profile(), rows)


def test_non_windows_runtime_makes_no_authenticode_claim(tmp_path):
    from scripts import stage_qualified_runtime as stage
    status = stage.authenticode_status(tmp_path, dict(platform='linux', files={'lib/x.so': {}}), [])
    assert status['authenticode_verified'] is False and status['authenticode_images'] == {}
