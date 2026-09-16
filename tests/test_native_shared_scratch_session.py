import copy
import json
from pathlib import Path, PureWindowsPath
import zipfile

import pytest

from scripts.audit_native_shared_scratch_session import (
    ORDER, check_loaded, check_profiles, norm, performance, readiness,
)
from scripts.audit_native_barge_in import barge_metrics

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / 'deliverables/native-worker-scratch-windows-20260913-r1/windows-evidence.zip'


def rows():
    return [{'arm': arm, 'ready_ms': 1000,
             'speech': [{'first_pcm_ms': 100, 'rtf': .5} for _ in range(4)]} for arm in ORDER]


@pytest.mark.parametrize('position', range(4))
@pytest.mark.parametrize('metric', ('first_pcm_ms', 'rtf'))
def test_each_reply_position_refuses_regression_despite_good_aggregate(position, metric):
    r = rows(); assert performance(r)['performance_gate_passed']
    for i in (1, 2):
        for s in r[i]['speech']: s[metric] *= .5
        r[i]['speech'][position][metric] = r[0]['speech'][position][metric] * 1.051
    assert not performance(r)['performance_gate_passed']


def test_ready_and_absolute_realtime_are_separate_admission_conditions():
    r = rows(); r[1]['ready_ms'] = 1101
    assert not performance(r)['performance_gate_passed']
    r = rows()
    for row in r:
        for speech in row['speech']: speech['rtf'] = 1.001
    assert not performance(r)['performance_gate_passed']


@pytest.mark.parametrize('damage', ('missing', 'duplicate', 'zero', 'wrong_phase'))
def test_readiness_requires_exactly_one_real_startup_observation(damage):
    row = {'component': 'voice-carrier-startup', 'phase': 'worker-readiness-validated', 'elapsed_ms': 45}
    good = json.dumps(row).encode(); assert readiness(good) == 45
    if damage == 'missing': bad = b'not JSON'
    elif damage == 'duplicate': bad = good + b'\n' + good
    else:
        row['elapsed_ms' if damage == 'zero' else 'phase'] = 0 if damage == 'zero' else 'not-readiness'
        bad = json.dumps(row).encode()
    with pytest.raises(AssertionError): readiness(bad)


def profile_fixture():
    with zipfile.ZipFile(PARENT) as z: prior = json.loads(z.read('run/result.json'))
    base = copy.deepcopy(prior['profiles']['candidate'])
    old = PureWindowsPath(base['worker']).parent
    new = PureWindowsPath('C:/isolated/candidate-bin')
    before = {**base['libraries'], 'aii_voice_worker.exe': prior['copy']['after']['aii_voice_worker.exe']}
    after = {**before, 'native_pocket_resident.dll': 'a' * 64}
    candidate = copy.deepcopy(base); candidate['worker'] = str(new / 'aii_voice_worker.exe')
    candidate['libraries']['native_pocket_resident.dll'] = 'a' * 64
    inv = {'source': str(old), 'destination': str(new), 'before': before, 'after': after}
    binds = {norm(folder) + '/' + name: h for folder, files in ((old, before), (new, after)) for name, h in files.items()}
    return prior, {'baseline': base, 'candidate': candidate}, inv, 'a' * 64, binds


@pytest.mark.parametrize('damage', ('none', 'model', 'worker', 'sdk', 'extra_dll', 'asr', 'unbound'))
def test_only_the_tts_image_may_change(damage):
    args = profile_fixture(); prior, profiles, inv, h, binds = args
    check_profiles(*args)
    if damage == 'none': return
    if damage == 'model': profiles['candidate']['model_paths'][0] += '-other'
    elif damage == 'worker': inv['after']['aii_voice_worker.exe'] = 'b' * 64
    elif damage == 'sdk': profiles['candidate']['carrier'] += '-other'
    elif damage == 'extra_dll': inv['after']['unexpected.dll'] = 'b' * 64
    elif damage == 'asr': profiles['candidate']['libraries']['aii_native_asr.dll'] = 'b' * 64
    else: binds[norm(inv['destination']) + '/native_pocket_resident.dll'] = 'b' * 64
    with pytest.raises(AssertionError): check_profiles(*args)


@pytest.mark.parametrize('damage', ('none', 'wrong_image', 'missing_image', 'alive', 'wrong_worker'))
def test_actual_loaded_images_and_retirement_not_only_profile_labels(damage):
    with zipfile.ZipFile(PARENT) as z:
        parent = json.loads(z.read('run/result.json'))
        row = json.loads(z.read('run/2-candidate/result.json'))
        absent = json.loads(z.read('retirement.json'))['absent_pids']
    profile = parent['profiles']['candidate']; bindings = {norm(p): h for p, h in parent['bindings'].items()}
    check_loaded(row, profile, bindings, absent)
    if damage == 'none': return
    if damage == 'wrong_image': row['loaded_worker']['bound_images']['native_pocket_resident.dll']['sha256'] = '0' * 64
    elif damage == 'missing_image': del row['loaded_worker']['bound_images']['aii_native_uid.dll']
    elif damage == 'alive': absent.remove(row['loaded_worker']['pid'])
    else: row['loaded_worker']['native_images']['aii_voice_worker.exe']['sha256'] = '0' * 64
    with pytest.raises(AssertionError): check_loaded(row, profile, bindings, absent)


@pytest.mark.parametrize('damage', ('none', 'words', 'revival', 'drain', 'cutoff', 'identity'))
def test_real_recorded_speech_proofs_refuse_missing_words_and_false_completion(damage):
    with zipfile.ZipFile(PARENT) as z: report = json.loads(z.read('run/2-candidate/conversation-0/report.json'))
    barge_metrics(report)
    if damage == 'none': return
    if damage == 'words': report['transcript'] = report['transcript'].split(' ', 1)[1]
    elif damage == 'revival':
        e = next(e for e in report['events'] if e['type'] == 'synthesis_cancelled')
        report['events'].append({**e, 'type': 'synthesis_end'})
    elif damage == 'drain': report['waiting_for_final_receipt']['lifecycle'] = 'closed'
    elif damage == 'cutoff': report['final_snapshot']['input_completion']['processed_end_sample'] -= 1
    else: report['events'][0]['session_id'] = 'another-session'
    with pytest.raises(AssertionError): barge_metrics(report)
