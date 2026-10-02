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
