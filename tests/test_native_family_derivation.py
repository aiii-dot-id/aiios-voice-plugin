import copy
import json
import pytest
import subprocess
import sys
from pathlib import Path
from scripts.derive_native_family_checkpoint import (
    OLD, NEW, WINDOWS_COEFFICIENTS, model_mapping, profile_bytes, validated_settings,
)


def test_exact_windows_model_moves_without_content_or_other_mapping_changes():
    before = {OLD: {'sha256': WINDOWS_COEFFICIENTS, 'bytes': 65920},
              'stt/model.safetensors': {'sha256': 'a' * 64, 'bytes': 100}}
    saved = copy.deepcopy(before)
    after = model_mapping(before)
    assert before == saved and OLD not in after and after[NEW] == saved[OLD]
    assert after['stt/model.safetensors'] == saved['stt/model.safetensors']
    for invalid in ({**before, NEW: before[OLD]}, {OLD: {'sha256': 'b' * 64}}):
        with pytest.raises(ValueError): model_mapping(invalid)


def test_native_path_only_changes_for_exact_windows_destination():
    before = {'backend': 'vulkan', 'models': {'endpoint_coefficients': OLD, 'tts': 'tts'}, 'uid_policy': 'a'}
    after = json.loads(profile_bytes(json.dumps(before).encode()))
    after['models']['endpoint_coefficients'] = OLD
    assert after == before
    before['models']['endpoint_coefficients'] = 'other'
    with pytest.raises(ValueError): profile_bytes(json.dumps(before).encode())


def test_settings_repair_only_the_proven_utf8_label():
    shared = [{'key': 'tts_voice', 'default': 'alba', 'labels': {'alba': 'Alba', 'eponine': 'Éponine'}}]
    raw = json.dumps(shared, ensure_ascii=False).encode()
    old = raw.replace('Éponine'.encode(), 'Ã‰ponine'.encode())
    assert validated_settings(old, raw) == raw
    assert validated_settings(raw, raw) == raw
    for old_raw in (old.replace(b'alba', b'other'), old.replace(b'Alba', b'Altered')):
        with pytest.raises(ValueError): validated_settings(old_raw, raw)


def test_owned_family_entrypoint_works_in_isolated_python():
    script = Path(__file__).resolve().parents[1] / 'scripts/prepare_native_family_windows.py'
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(script), '--help'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert '--source' in result.stdout and '--out' in result.stdout
