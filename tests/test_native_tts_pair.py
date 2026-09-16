import copy
import json
from pathlib import Path
import zipfile
import pytest
from scripts.audit_native_tts_pair import speech_metrics
from scripts.compare_native_composition_tts_windows import timing_gate

ROOT = Path(__file__).resolve().parents[1]


def actual():
    with zipfile.ZipFile(ROOT / 'deliverables/native-transpose-free-session-windows-20260913-r1/windows-evidence.zip') as z:
        return json.loads(z.read('run/1-baseline/spoken-regression/report.json'))


def test_actual_recorded_barge_in_has_complete_timing_evidence():
    rows = speech_metrics(actual())
    assert len(rows) == 2 and [r['samples'] for r in rows] == [42240, 90240]


@pytest.mark.parametrize('defect', ['words', 'barge', 'tail', 'receipt'])
def test_incomplete_voice_cannot_supply_timing_evidence(defect):
    r = actual()
    if defect == 'words':
        r['transcript'] = 'missing opening words'
    elif defect == 'barge':
        r['events'] = [e for e in r['events'] if e['type'] != 'interruption_requested']
    elif defect == 'tail':
        r['frame_spans'] = r['frame_spans'][:-1]
    else:
        r['receipts'] = []
    with pytest.raises(AssertionError):
        speech_metrics(r)


def rows():
    return [{'arm': arm, 'speech': [{'first_pcm_ms': 400., 'rtf': .5} for _ in range(4)]}
            for arm in ('baseline', 'candidate', 'candidate', 'baseline')]


def test_a_bad_reply_position_is_not_averaged_away():
    r = rows()
    assert timing_gate(r)['performance_gate_passed']
    r[1]['speech'][0]['first_pcm_ms'] *= 1.2
    assert not timing_gate(r)['performance_gate_passed']
    r = rows()
    r[1]['speech'][0]['rtf'] = 1.01
    assert not timing_gate(r)['all_candidate_replies_faster_than_real_time']


def test_run_order_and_missing_reply_refused():
    r = rows()
    r[2]['arm'] = 'baseline'
    with pytest.raises(AssertionError):
        timing_gate(r)
    r = rows()
    r[0]['speech'].pop()
    with pytest.raises(AssertionError):
        timing_gate(r)


def component():
    from scripts.native_tts_component import component_spec
    parent = ROOT / 'deliverables/native-tts-fixed-step-storage-windows-20260913-r1'
    audit = json.loads((parent / 'independent-audit-r1.json').read_text())
    with zipfile.ZipFile(parent / 'transfer/source.zip') as z:
        contract = json.loads(z.read('contract.json'))
    with zipfile.ZipFile(parent / 'windows-evidence.zip') as z:
        result = json.loads(z.read('run/result.json'))
    return audit, contract, result, component_spec(audit, contract, result)


def profiles():
    with zipfile.ZipFile(ROOT / 'deliverables/native-transpose-free-session-windows-20260913-r1/windows-evidence.zip') as z:
        return {arm: json.loads(z.read('run/' + arm + '/preparation.json')) for arm in ('baseline', 'candidate')}


@pytest.mark.parametrize('defect', ['failed', 'slow', 'memory', 'different-kind', 'ambiguous-image'])
def test_failed_or_different_tts_component_is_not_admitted(defect):
    from scripts.native_tts_component import component_spec
    a, c, r, _ = component()
    if defect == 'failed':
        a['passed'] = False
    elif defect == 'slow':
        a['measured']['performance_gate_passed'] = False
    elif defect == 'memory':
        a['measured']['memory_gate_passed'] = False
    elif defect == 'different-kind':
        c['candidate'] = 'persistent-graph-storage'
    else:
        r['bindings']['C:/home/user/work'] = r['libraries']['candidate']
    with pytest.raises(AssertionError):
        component_spec(a, c, r)


@pytest.mark.parametrize('defect', [None, 'carrier', 'uid', 'model', 'library', 'setting', 'worker'])
def test_combined_profile_allows_exact_tts_replacement_only(defect):
    from scripts.native_tts_component import derive_profile, validate_profiles
    original = profiles()
    spec = component()[-1]
    worker = r'C:\owned\run\combined-bin\aii_voice_worker.exe'
    effective = copy.deepcopy(original)
    effective['candidate'] = derive_profile(original['candidate'], worker, spec)
    if defect == 'carrier':
        effective['candidate']['carrier'] = 'another carrier'
    elif defect == 'uid':
        effective['candidate']['uid_model'] = 'another model'
    elif defect == 'model':
        effective['candidate']['model_paths'][5] = 'another TTS model'
    elif defect == 'library':
        effective['candidate']['libraries']['aii_native_vad.dll'] = 'another DLL'
    elif defect == 'setting':
        effective['candidate']['threads'] = 1
    elif defect == 'worker':
        effective['candidate']['worker'] = 'another worker'
    if defect:
        with pytest.raises(AssertionError):
            validate_profiles(original, effective, spec, worker)
    else:
        validate_profiles(original, effective, spec, worker)
        validate_profiles(original, original)


@pytest.mark.parametrize('defect', [None, 'omitted', 'extra', 'worker'])
def test_combined_copy_rejects_unrelated_file_change(defect):
    from scripts.native_tts_component import validate_copy, TTS
    before = {TTS: 'old', 'aii_voice_worker.exe': 'unchanged', 'vad.dll': 'same'}
    spec = component()[-1]
    after = dict(before, **{TTS: spec['sha256']})
    if defect == 'omitted':
        del after['vad.dll']
    elif defect == 'extra':
        after['another.dll'] = 'unbound'
    elif defect == 'worker':
        after['aii_voice_worker.exe'] = 'changed'
    if defect:
        with pytest.raises(AssertionError):
            validate_copy(before, after, spec)
    else:
        validate_copy(before, after, spec)
