import copy
import json
from pathlib import Path
import zipfile
import pytest
from scripts.audit_native_barge_in import barge_metrics

ROOT = Path(__file__).resolve().parents[1]


def report():
    with zipfile.ZipFile(ROOT / 'deliverables/native-tts-composition-pair-windows-20260913-r2/windows-evidence.zip') as z:
        return json.loads(z.read('run/1-baseline/conversation-0/report.json'))


def test_actual_recorded_interruption_has_opening_words_and_recovery():
    measured = barge_metrics(report())
    assert 0 < measured['vad_request_to_cancel_ms'] < 100
    assert not measured['physical_playback_stop_latency_measured']


@pytest.mark.parametrize('defect', ['restart', 'unheard-reply', 'stale-delivery', 'false-stop', 'wrong-session', 'wrong-observation', 'lost-input-tail', 'lost-opening', 'final-mismatch', 'no-recovery'])
def test_presence_of_a_cancel_event_is_not_barge_in_proof(defect):
    r = report()
    if defect == 'restart':
        start = next(e for e in r['events'] if e['type'] == 'synthesis_start' and e['output_stream'] == 2)
        r['events'].insert(-1, copy.deepcopy(start))
        for seq, e in enumerate(r['events'], 1):
            e['sequence'] = seq
    elif defect == 'unheard-reply':
        r['interrupt_first_pcm'] = r['input_feed_begin_elapsed'] + 1
    elif defect == 'stale-delivery':
        next(e for e in r['events'] if e['type'] == 'synthesis_cancelled')['delivered_samples'] -= 1
    elif defect == 'false-stop':
        r['receipts'][1]['observation']['outcome'] = 'drained'
    elif defect == 'wrong-session':
        r['receipts'][1]['arguments']['session_id'] = 'another-session'
    elif defect == 'wrong-observation':
        r['receipts'][1]['observation']['synthesis_id'] = 'another-generation'
    elif defect == 'lost-input-tail':
        next(e for e in r['events'] if e['type'] == 'input_finished')['processed_end_sample'] -= 1
    elif defect == 'lost-opening':
        r['transcript'] = r['transcript'][20:]
    elif defect == 'final-mismatch':
        next(e for e in r['events'] if e['type'] == 'transcript_final')['text'] = 'invented words'
    else:
        r['events'] = [e for e in r['events'] if not(e['type'] == 'synthesis_end' and e['output_stream'] == 3)]
    with pytest.raises(AssertionError):
        barge_metrics(r)
