"""The candidate changes idle thread behavior, not math, integrity, or gates."""
from pathlib import Path
import pytest
from scripts.stage_native_no_spin import transform
from scripts.compare_native_precomputed_windows import candidate_flag
from scripts.audit_native_precomputed_windows import validate_candidate

ROOT = Path(__file__).resolve().parents[1]


def test_exact_one_block_and_no_removed_bytes():
    before = (ROOT / 'runtime/native_asr/asr.cpp').read_text()
    after = transform('asr.cpp', before)
    start = after.index('#if defined(AII_ASR_NO_SPIN)')
    end = after.index('#endif', start) + len('#endif\n')
    assert after[:start] + after[end:] == before
    assert after.count('allow_spinning') == 2
    assert '"session.intra_op.allow_spinning", "0"' in after
    assert '"session.inter_op.allow_spinning", "0"' in after


def test_explicit_off_by_default_native_flag():
    source = (ROOT / 'runtime/native_asr/CMakeLists.txt').read_text()
    out = transform('CMakeLists.txt', source)
    assert 'option(AII_ASR_NO_SPIN "Isolated sleeping idle worker candidate" OFF)' in out
    assert 'target_compile_definitions(aii_native_asr PRIVATE AII_ASR_NO_SPIN=1)' in out
    assert candidate_flag('no-spin') == 'AII_ASR_NO_SPIN'


@pytest.mark.parametrize('source', ['', '    options.SetExecutionMode(ORT_SEQUENTIAL);\n'*2,
                                     'AII_ASR_NO_SPIN\n    options.SetExecutionMode(ORT_SEQUENTIAL);'])
def test_changed_or_already_patched_source_refused(source):
    with pytest.raises(ValueError):
        transform('asr.cpp', source)


def test_unknown_flag_refused():
    with pytest.raises(KeyError):
        candidate_flag('typo')


def test_independent_audit_requires_kind_and_each_build_flag():
    def result():
        return {'candidate_kind': 'no-spin', 'stages': [
            {'name': 'baseline-configure', 'command': ['-DAII_ASR_NO_SPIN=OFF']},
            {'name': 'candidate-configure', 'command': ['-DAII_ASR_NO_SPIN=ON']}]}
    validate_candidate(result(), 'no-spin')
    for change in ('mislabeled', 'candidate-off', 'baseline-on'):
        broken = result()
        if change == 'mislabeled': broken['candidate_kind'] = 'precomputed'
        elif change == 'candidate-off': broken['stages'][1]['command'] = ['-DAII_ASR_NO_SPIN=OFF']
        else: broken['stages'][0]['command'] = ['-DAII_ASR_NO_SPIN=ON']
        with pytest.raises(AssertionError): validate_candidate(broken, 'no-spin')
