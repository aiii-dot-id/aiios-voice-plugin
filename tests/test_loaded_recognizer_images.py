import json
import pytest
from scripts.native_loaded_images import recognizer_images
from scripts.native_checkpoint_binding import sha

def fixture(root,execution):
    binary=root/'bin';binary.mkdir()
    profile=root/'native-profile.json'
    profile.write_text(json.dumps({'asr_execution':execution}))
    (root/'voice-runtime.json').write_text(json.dumps({'files':{'native-profile.json':dict(sha256=sha(profile),bytes=profile.stat().st_size)}}))
    return binary,profile

@pytest.mark.parametrize('platform',['linux','windows'])
def test_sealed_nemotron_requires_its_own_images(tmp_path,platform):
    binary,_=fixture(tmp_path,{'diarizer':'nemotron'})
    names=recognizer_images(binary,platform)
    assert len(names)==2 and all('nemo_speech_asr' in n for n in names)
    assert not any('aii_native_asr' in n for n in names)

def test_legacy_and_tampered_composition(tmp_path):
    binary,profile=fixture(tmp_path,None)
    assert recognizer_images(binary,'windows')=={'aii_native_asr.dll'}
    profile.write_text('{}')
    with pytest.raises(ValueError):recognizer_images(binary,'windows')

def test_unknown_composition_does_not_assume_legacy(tmp_path):
    binary,_=fixture(tmp_path,{'diarizer':'unknown'})
    with pytest.raises(ValueError):recognizer_images(binary,'windows')


def test_a_windows_process_is_a_child_only_if_it_was_created_after_its_parent():
    """Windows keeps a dead parent's number in its children and gives the number out again: a program started
    long before the carrier can carry the carrier's number as its parent's. The listing asks for the parent's
    creation time and takes only what was created no earlier."""
    from scripts import native_loaded_images as images
    command = images.windows_children_command(4242)
    assert "-Filter 'ProcessId = 4242'" in command and "-Filter 'ParentProcessId = 4242'" in command
    assert '$_.CreationDate -ge $p.CreationDate' in command and '$p -and' in command
    asked = []

    def run(argv, **how):
        asked.append((argv, how))
        return run.says
    run.says = '{"ProcessId":7,"ExecutablePath":"C:/r/bin/aii_voice_worker.exe"}\n'
    assert images.windows_children(4242, run) == [dict(ProcessId=7, ExecutablePath='C:/r/bin/aii_voice_worker.exe')]
    assert asked[0][0][-1] == command and asked[0][1] == dict(text=True, timeout=20)
    run.says = '[{"ProcessId":7,"ExecutablePath":"a"},{"ProcessId":8,"ExecutablePath":"b"}]'
    assert [child['ProcessId'] for child in images.windows_children(4242, run)] == [7, 8]
    run.says = '\n'
    assert images.windows_children(4242, run) == []
