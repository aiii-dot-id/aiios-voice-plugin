"""Nothing in scripts/ builds, stages, binds, packages or assembles a Python engine.

The engine is the native worker. A Python engine's runtime profile named an interpreter, a
bootstrap script and a site directory in three members (python, bootstrap, site), and a
packer wrote such a profile. The carrier refuses one at its start; the packer and the
bootstrap are removed. This holds that state:

  the packer and its bootstrap are gone, and with neither option the tool that held the
  packer says it packs nothing and writes nothing;

  a runtime whose profile states one of the three members can still be VERIFIED. verify()
  answers whether a runtime's bytes are what its profile binds, and it answers that for an
  old Python engine's runtime as for any other: whole, it is returned; changed, it differs.
  Checking what exists is not a way to start, ship or build it;

  everything that would MAKE something of such a runtime REFUSES it, in the one sentence of
  scripts/package_native_runtime.py, where it reads the profile it would build on:
    bind_carrier()    before any toolchain is run; no carrier and no record are written
    rebuild_native_checkpoint.main and stage_nemotron_macos.main
                      at the parent each reads; no output directory is made
    rebind_signed_windows_runtime.prepare
                      at the parent it reads; no output directory is made
    stage_qualified_runtime.main
                      at the checkpoint it would make a release archive of; no output is made
    package_common_native_checkpoint.main
                      at the checkpoint it would assemble a package from; its output stays empty
    restore_staged_native_checkpoint.restore
                      at the staged profile, before the archive is unpacked
    assemble_guided_beta_candidate.runtime
                      at the stage it reads, with the stage named

  a native profile, which leaves the three out or states them empty, is not refused by
  any of them: each goes on to the next thing it needs, and a rebuild writes a profile
  whose three members are as empty as its parent's.

No script chooses "empty members" by clearing what a parent states: a parent that
describes an interpreter is not a parent. The scripts that write a profile and the scripts
that pack a runtime archive are named here, so one that starts to do either is driven here
too; every carrier is bound through bind_carrier(), which refuses by itself.
"""
import hashlib
import inspect
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts import package_native_runtime as runtimes
from scripts.package_native_runtime import (INTERPRETER_MEMBERS, bind_carrier, refuse_interpreter_profile,
                                            runtime_inventory, sha256, verify)
from tests.test_native_rebuild_libraries import NEMO, nemo_sources, run_rebuild, sealed_windows_parent

ROOT = Path(__file__).resolve().parents[1]
REFUSED = 'describes a Python engine'

# What a Python engine's profile stated, each member alone and all together.
INTERPRETER = dict(python='python/bin/python3.11', bootstrap='engine/plugin/runtime_bootstrap.py',
                   site='python/lib/python3.11/site-packages')
DESCRIBED = [pytest.param({name: INTERPRETER[name]}, id=name) for name in INTERPRETER_MEMBERS] + [
    pytest.param(dict(INTERPRETER), id='all three')]
# What a native profile states of the three: nothing, or each empty.
EMPTY = dict.fromkeys(INTERPRETER_MEMBERS, '')
NATIVE = [pytest.param({}, id='left out'), pytest.param(dict(EMPTY), id='stated empty')]


def snapshot(root):
    return {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}


def state(parent, members):
    """Give a sealed parent's profile these members, and rebind the records that name the profile."""
    path = parent / 'runtime/voice-runtime.json'
    path.write_text(json.dumps({**json.loads(path.read_text()), **members}))
    for name in ('freeze.json', 'carrier-build.json'):
        record = json.loads((parent / name).read_text())
        record['runtime_manifest_sha256'] = sha256(path)
        (parent / name).write_text(json.dumps(record))


def sealed_parent(tmp_path, platform, arch, members):
    """A small sealed parent of one platform whose profile also states these members."""
    parent = tmp_path / 'parent'
    runtime = parent / 'runtime'
    carrier = 'aii-voice-t3.exe' if platform == 'windows' else 'aii-voice-t3'
    (runtime / 'bin').mkdir(parents=True)
    (runtime / 'bin/aii_voice_worker').write_bytes(b'native worker')
    (runtime / carrier).write_bytes(b'parent carrier')
    profile = dict(schema='aiii.voice.native-runtime', platform=platform, arch=arch, qualified=False,
                   files=runtime_inventory(runtime, target_platform=platform), **members)
    (runtime / 'voice-runtime.json').write_text(json.dumps(profile))
    models = tmp_path / 'models'
    models.mkdir()
    (models / 'model.bin').write_bytes(b'unchanged model')
    manifest, carrier_digest = sha256(runtime / 'voice-runtime.json'), sha256(runtime / carrier)
    (parent / 'carrier-build.json').write_text(json.dumps(dict(
        carrier_sha256=carrier_digest, runtime_manifest_sha256=manifest)))
    (parent / 'freeze.json').write_text(json.dumps(dict(
        passed=True, signed=False, installed=False, runtime_manifest_sha256=manifest, carrier_sha256=carrier_digest,
        models_root=str(models), models={'model.bin': dict(bytes=15, sha256=sha256(models / 'model.bin'))},
        library_hashes={})))
    return parent


def sealed_runtime(root, members):
    """A small runtime bound by its profile; the profile's digest."""
    (root / 'bin').mkdir(parents=True)
    (root / 'bin/aii_voice_worker').write_bytes(b'native worker')
    profile = dict(schema='aiii.voice.native-runtime', qualified=False, platform='linux', arch='amd64',
                   files=runtime_inventory(root, target_platform='linux'), **members)
    (root / 'voice-runtime.json').write_text(json.dumps(profile))
    return sha256(root / 'voice-runtime.json')


def staged_profile(directory, members):
    """An archive that holds a runtime profile and nothing else; the archive and the profile's bytes."""
    directory.mkdir()
    raw = json.dumps(dict(schema='aiii.voice.native-runtime', qualified=False, platform='linux', arch='amd64',
                          files={}, **members)).encode()
    archive = directory / 'runtime.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        entry = tarfile.TarInfo('runtime/voice-runtime.json')
        entry.size = len(raw)
        tar.addfile(entry, io.BytesIO(raw))
    return archive, raw


# 1. What is gone, and which scripts are held here.

def test_the_packer_and_its_bootstrap_are_gone(tmp_path):
    assert not (ROOT / 'plugin/runtime_bootstrap.py').exists(), 'the packaged interpreter\'s bootstrap is back'
    naming = sorted(path.name for path in (ROOT / 'scripts').glob('*.py') if 'runtime_bootstrap' in path.read_text())
    assert not naming, f'scripts name the bootstrap: {naming}'
    public = {name for name, value in vars(runtimes).items()
              if inspect.isfunction(value) and value.__module__ == runtimes.__name__ and not name.startswith('_')}
    # Every function here is driven below with a profile that describes an interpreter, or takes no profile.
    assert public == {'bind_carrier', 'refuse_interpreter_profile', 'runtime_inventory', 'safe_relative',
                      'sha256', 'verify'}, sorted(public)
    asked = subprocess.run([sys.executable, '-m', 'scripts.package_native_runtime', '--output', str(tmp_path / 'packed')],
                           cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert asked.returncode == 2 and 'no longer packs a runtime' in asked.stderr, asked.stderr
    assert not (tmp_path / 'packed').exists()


def test_the_scripts_that_write_a_runtime_profile_are_the_ones_driven_here():
    writes = re.compile(r"""voice-runtime\.json['"]\)\s*\.write_""")
    writers = {path.stem for path in (ROOT / 'scripts').glob('*.py') if writes.search(path.read_text())}
    assert writers == {'rebuild_native_checkpoint', 'stage_nemotron_macos', 'rebind_signed_windows_runtime'}, (
        'a script writes a runtime profile and is not driven here with a parent that describes an interpreter: '
        + str(sorted(writers)))


def test_the_scripts_that_pack_a_runtime_archive_are_the_ones_driven_here():
    # The two that run the kit's runtime-pack on a checkpoint, and the assembler that reads what was staged.
    packers = {path.stem for path in (ROOT / 'scripts').glob('*.py') if 'runtime-pack' in path.read_text()}
    assert packers == {'stage_qualified_runtime', 'package_common_native_checkpoint',
                       'assemble_guided_beta_candidate'}, (
        'a script packs a runtime archive and is not driven here with a checkpoint that describes an interpreter: '
        + str(sorted(packers)))


# 2. The one sentence, and what verify() still answers.

@pytest.mark.parametrize('members', DESCRIBED)
def test_the_refusal_names_what_the_profile_states_in_one_sentence(members):
    with pytest.raises(ValueError, match=REFUSED) as refusal:
        refuse_interpreter_profile(members)
    said = str(refusal.value)
    assert all(name in said for name in members) and '\n' not in said
    # It is the refusal of what makes something. Verifying is not refused, and the sentence does not say it is.
    assert 'rebuilds, stages, binds or assembles' in said and 'verif' not in said


@pytest.mark.parametrize('members', DESCRIBED)
def test_an_intact_runtime_that_describes_an_interpreter_still_verifies(tmp_path, members):
    root = tmp_path / 'runtime'
    digest = sealed_runtime(root, members)
    before = snapshot(tmp_path)
    profile = verify(root, digest)
    assert profile == json.loads((root / 'voice-runtime.json').read_text())
    assert {name: profile[name] for name in members} == members
    assert snapshot(tmp_path) == before
    # And it is still held to its profile: a changed byte differs, for this runtime as for any other.
    (root / 'bin/aii_voice_worker').write_bytes(b'another worker')
    with pytest.raises(ValueError, match='differs') as changed:
        verify(root, digest)
    assert REFUSED not in str(changed.value)


@pytest.mark.parametrize('members', NATIVE)
def test_a_native_profile_is_read(tmp_path, members):
    refuse_interpreter_profile(members)
    digest = sealed_runtime(tmp_path / 'runtime', members)
    assert verify(tmp_path / 'runtime', digest)['files'] == runtime_inventory(tmp_path / 'runtime', target_platform='linux')


# 3. Binding a carrier.

@pytest.mark.parametrize('members', DESCRIBED)
def test_no_carrier_is_bound_to_a_runtime_that_describes_an_interpreter(tmp_path, members):
    sealed_runtime(tmp_path / 'runtime', members)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match=REFUSED):
        bind_carrier(tmp_path / 'runtime', tmp_path / 'carrier-build.json', tmp_path / 'no-go-here')
    assert snapshot(tmp_path) == before, 'something was written for a runtime no carrier starts'


@pytest.mark.parametrize('members', NATIVE)
def test_binding_a_native_runtime_goes_on_to_its_toolchain(tmp_path, members):
    sealed_runtime(tmp_path / 'runtime', members)
    # Not refused: binding goes on to the pinned kit source and the Go named, neither of which is supplied here.
    with pytest.raises(FileNotFoundError):
        bind_carrier(tmp_path / 'runtime', tmp_path / 'carrier-build.json', tmp_path / 'no-go-here')
    assert not (tmp_path / 'carrier-build.json').exists()


# 4. The scripts that write a profile from a parent's.

def changed_nemo(tmp_path):
    return [argument for source in nemo_sources(tmp_path, NEMO['windows']) for argument in ('--nemo', str(source))]


@pytest.mark.parametrize('members', DESCRIBED)
def test_nothing_is_rebuilt_from_a_parent_that_describes_an_interpreter(tmp_path, monkeypatch, members):
    parent, _ = sealed_windows_parent(tmp_path)
    state(parent, members)
    before = snapshot(parent)
    with pytest.raises(ValueError, match=REFUSED):
        run_rebuild(tmp_path, monkeypatch, parent, changed_nemo(tmp_path))
    assert not (tmp_path / 'candidate').exists() and snapshot(parent) == before


@pytest.mark.parametrize('members', NATIVE)
def test_a_rebuild_of_a_native_parent_writes_no_interpreter_member(tmp_path, monkeypatch, members):
    parent, _ = sealed_windows_parent(tmp_path)
    state(parent, members)
    out, _ = run_rebuild(tmp_path, monkeypatch, parent, changed_nemo(tmp_path))
    written = json.loads((out / 'runtime/voice-runtime.json').read_text())
    # What the parent states of the three is carried as it is: left out, or empty.
    assert {name: written[name] for name in INTERPRETER_MEMBERS if name in written} == members
    refuse_interpreter_profile(written)


def mac_stage(tmp_path, monkeypatch, parent):
    """Ask the Mac stage for a set from this parent, with none of its other inputs there."""
    from scripts import stage_nemotron_macos as stage
    monkeypatch.setattr(sys, 'argv', [
        'stage_nemotron_macos', '--parent', str(parent), '--parent-sha256', sha256(parent / 'freeze.json'),
        '--build', str(tmp_path / 'build'), '--nemo', str(tmp_path / 'nemo'), '--ort', str(tmp_path / 'ort'),
        '--model', str(tmp_path / 'model'), '--model-sha256', '0' * 64, '--out', str(tmp_path / 'staged'),
        '--go', str(tmp_path / 'go')])
    stage.main()


@pytest.mark.parametrize('members', DESCRIBED)
def test_nothing_is_staged_on_the_mac_from_a_parent_that_describes_an_interpreter(tmp_path, monkeypatch, members):
    parent = sealed_parent(tmp_path, 'darwin', 'arm64', members)
    before = snapshot(parent)
    with pytest.raises(ValueError, match=REFUSED):
        mac_stage(tmp_path, monkeypatch, parent)
    assert not (tmp_path / 'staged').exists() and snapshot(parent) == before


def test_the_mac_stage_reads_a_native_parent_and_goes_on_to_its_model(tmp_path, monkeypatch):
    parent = sealed_parent(tmp_path, 'darwin', 'arm64', EMPTY)
    with pytest.raises(FileNotFoundError):  # the model this test does not supply; the parent was read
        mac_stage(tmp_path, monkeypatch, parent)
    assert not (tmp_path / 'staged').exists()


@pytest.mark.parametrize('members', DESCRIBED)
def test_no_signed_windows_set_is_rebound_from_a_parent_that_describes_an_interpreter(tmp_path, members):
    from scripts.rebind_signed_windows_runtime import prepare
    parent = sealed_parent(tmp_path, 'windows', 'amd64', members)
    before = snapshot(parent)
    with pytest.raises(ValueError, match=REFUSED):
        prepare(parent, tmp_path / 'signed', tmp_path / 'rebound', tmp_path / 'go', tmp_path / 'signtool')
    assert not (tmp_path / 'rebound').exists() and snapshot(parent) == before


def test_a_signing_rebind_reads_a_native_parent_and_goes_on_to_its_signing_stage(tmp_path):
    from scripts.rebind_signed_windows_runtime import prepare
    parent = sealed_parent(tmp_path, 'windows', 'amd64', EMPTY)
    with pytest.raises(FileNotFoundError):  # the signing stage this test does not supply; the parent was read
        prepare(parent, tmp_path / 'signed', tmp_path / 'rebound', tmp_path / 'go', tmp_path / 'signtool')
    assert not (tmp_path / 'rebound').exists()


# 5. The scripts that make a release archive or a package of a checkpoint.

def checkpoint_of(tmp_path, members):
    """A checkpoint whose sealed runtime's profile also states these members; the profile's digest."""
    checkpoint = tmp_path / 'checkpoint'
    digest = sealed_runtime(checkpoint / 'runtime', members)
    (checkpoint / 'runtime/aii-voice-t3').write_bytes(b'carrier')
    (checkpoint / 'freeze.json').write_text(json.dumps(dict(passed=True, runtime_manifest_sha256=digest)))
    return checkpoint, digest


def qualified_stage(tmp_path, monkeypatch, members):
    """Ask staging for a release archive of this checkpoint, with a record that its runtime passed."""
    from scripts import stage_qualified_runtime as stage
    checkpoint, digest = checkpoint_of(tmp_path, members)
    audit = tmp_path / 'audit.json'
    audit.write_text(json.dumps(dict(passed=True, runtime_manifest_sha256=digest)))
    monkeypatch.setattr(sys, 'argv', [
        'stage_qualified_runtime', '--checkpoint', str(checkpoint), '--audit', str(audit),
        '--audit-sha256', sha256(audit), '--runtime-sha256', digest, '--out', str(tmp_path / 'staged'),
        '--go-modcache', str(tmp_path / 'modcache'), '--max-compressed-bytes', '1000000'])
    stage.main()


@pytest.mark.parametrize('members', DESCRIBED)
def test_no_release_archive_is_staged_of_a_checkpoint_that_describes_an_interpreter(tmp_path, monkeypatch, members):
    with pytest.raises(ValueError, match=REFUSED):
        qualified_stage(tmp_path, monkeypatch, members)
    assert not (tmp_path / 'staged').exists()


def test_staging_reads_a_native_checkpoint_and_goes_on_to_its_limits(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='states no time limits'):  # the next thing a staged profile must state
        qualified_stage(tmp_path, monkeypatch, EMPTY)
    assert not (tmp_path / 'staged').exists()


def common_package(tmp_path, monkeypatch, members):
    """Ask for a test package of this checkpoint, with none of the proofs it needs beside it."""
    from scripts import package_common_native_checkpoint as package
    checkpoint, _ = checkpoint_of(tmp_path, members)
    monkeypatch.setattr(sys, 'argv', [
        'package_common_native_checkpoint', '--checkpoint', str(checkpoint), '--proof', str(tmp_path / 'proof'),
        '--out', str(tmp_path / 'package')])
    package.main()


@pytest.mark.parametrize('members', DESCRIBED)
def test_no_package_is_assembled_from_a_checkpoint_that_describes_an_interpreter(tmp_path, monkeypatch, members):
    with pytest.raises(ValueError, match=REFUSED):
        common_package(tmp_path, monkeypatch, members)
    # The script makes its output directory before it reads anything; nothing is put in it.
    assert not list((tmp_path / 'package').iterdir())


def test_packaging_reads_a_native_checkpoint_and_goes_on_to_its_proofs(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError):  # the proofs this test does not supply; the checkpoint was read
        common_package(tmp_path, monkeypatch, EMPTY)
    assert not list((tmp_path / 'package').iterdir())


# 6. The scripts that carry a staged profile on.

def stage_record(tmp_path, members):
    archive, raw = staged_profile(tmp_path / 'stage', members)
    record = tmp_path / 'stage.json'
    record.write_text(json.dumps(dict(
        passed=True, models_in_archive=False, models={}, runtime_manifest_sha256=hashlib.sha256(raw).hexdigest(),
        runtime_archive=dict(sha256=sha256(archive), size=archive.stat().st_size))))
    return record, archive


@pytest.mark.parametrize('members', DESCRIBED)
def test_no_checkpoint_is_restored_from_a_stage_that_describes_an_interpreter(tmp_path, members):
    from scripts.restore_staged_native_checkpoint import restore
    record, archive = stage_record(tmp_path, members)
    with pytest.raises(ValueError, match=REFUSED):
        restore(record, archive, tmp_path, tmp_path / 'restored', tmp_path / 'go')
    assert not (tmp_path / 'restored').exists()


def test_a_restore_reads_a_native_stage_and_goes_on_to_its_archive(tmp_path):
    from scripts.restore_staged_native_checkpoint import restore
    record, archive = stage_record(tmp_path, EMPTY)
    with pytest.raises(Exception) as other:  # the archive holds no inventory; the profile was read
        restore(record, archive, tmp_path, tmp_path / 'restored', tmp_path / 'go')
    assert REFUSED not in str(other.value) and not (tmp_path / 'restored').exists()


def staged_for_assembly(tmp_path, members):
    archive, raw = staged_profile(tmp_path / 'stage', members)
    (tmp_path / 'stage/result.json').write_text(json.dumps(dict(
        passed=True, installed=False, published=False, runtime_manifest_sha256=hashlib.sha256(raw).hexdigest(),
        runtime_archive=dict(path=archive.name))))
    return tmp_path / 'stage'


@pytest.mark.parametrize('members', DESCRIBED)
def test_no_stage_that_describes_an_interpreter_is_assembled(tmp_path, members):
    from scripts import assemble_guided_beta_candidate as assembly
    stage = staged_for_assembly(tmp_path, members)
    with pytest.raises(ValueError, match=REFUSED) as refusal:
        assembly.runtime(stage)
    assert str(refusal.value).endswith(str(stage)), 'the refusal does not say which stage'


def test_assembly_reads_a_native_stage_and_goes_on_to_its_limits(tmp_path):
    from scripts import assemble_guided_beta_candidate as assembly
    stage = staged_for_assembly(tmp_path, EMPTY)
    with pytest.raises(ValueError, match='states no time limits'):  # the next thing a stage must state
        assembly.runtime(stage)
