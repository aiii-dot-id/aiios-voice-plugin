import array
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import audit_native_delivery_timing as readback
from scripts.audit_native_delivery_timing import check_row
from scripts.probe_native_delivery_timing import TimedBackend


def test_observer_preserves_same_stream_results_and_retirement():
    audio = object()
    outcomes = iter([audio, None])
    closed = []
    source = SimpleNamespace(next=lambda: next(outcomes), close=lambda: closed.append(True))
    texts = []

    def start(text):
        texts.append(text)
        return source

    observer = TimedBackend(SimpleNamespace(tts_stream=start))
    stream = observer.tts_stream('unchanged text')
    assert stream.next() is audio
    assert stream.next() is None
    stream.close()
    assert texts == ['unchanged text'] and closed == [True]
    assert [r['kind'] for r in observer.calls] == ['start', 'next', 'next', 'close']
    assert all(r['end'] >= r['start'] for r in observer.calls)


def test_observer_records_and_reraises_identical_failure():
    failure = RuntimeError('native failure')

    def fail():
        raise failure

    observer = TimedBackend(None)
    with pytest.raises(RuntimeError) as caught:
        observer.call('next', fail)
    assert caught.value is failure
    assert len(observer.calls) == 1 and observer.calls[0]['kind'] == 'next'


def evidence_row(tmp_path):
    pcm = array.array('f', [0.0] * 1920).tobytes()
    name = '0-direct-0.f32'
    (tmp_path / name).write_bytes(pcm)
    calls = [{'kind': kind, 'start': start, 'end': end} for kind, start, end in (
        ('start', .001, .011), ('next', .012, .042),
        ('next', .044, .046), ('close', .047, .048))]
    native = sum(r['end'] - r['start'] for r in calls)
    return {'index': 0, 'lane': 'direct', 'text_index': 0,
            'text': 'The local voice service is ready.', 'pcm_file': name,
            'pcm_sha256': hashlib.sha256(pcm).hexdigest(), 'samples': 1920,
            'audio_seconds': .08, 'marks': [.043], 'first_pcm_seconds': .043,
            'calls': calls, 'elapsed_seconds': .05, 'adapter_seconds': native,
            'outside_adapter_seconds': .05 - native, 'rtf': .05 / .08,
            'adapter_rtf': native / .08}


def test_readback_recomputes_native_and_delivery_attribution(tmp_path):
    row = evidence_row(tmp_path)
    result = check_row(tmp_path, row, 0, 'direct', 0)
    assert result['rtf'] == .625
    assert result['outside_adapter_ms'] == pytest.approx(7)


@pytest.mark.parametrize('key, value, error', [
    ('adapter_seconds', .001, 'attribution'),
    ('elapsed_seconds', .02, 'coverage'),
    ('rtf', .1, 'factor'),
    ('samples', 1921, 'count'),
    ('first_pcm_seconds', .001, 'first PCM'),
    ('pcm_file', '../unbound.f32', 'path'),
])
def test_false_faster_timing_is_refused(tmp_path, key, value, error):
    row = evidence_row(tmp_path)
    row[key] = value
    with pytest.raises(ValueError, match=error):
        check_row(tmp_path, row, 0, 'direct', 0)


ROOT = Path(__file__).resolve().parents[1] / 'deliverables/native-delivery-timing-20260910-r1/evidence-r1/native-delivery-timing-r2'


def test_recorded_native_and_resident_comparisons_are_complete():
    result = readback.audit(ROOT)
    assert result['evidence_valid'] and not result['qualified'] and not result['promoted']
    assert sum(len(r['rows']) for r in result['results']) == 32
    assert all(r['exact_pcm_all_eight'] for r in result['paired_package_audio'])


@pytest.mark.parametrize('damage, expected', [('source', 'observer source'), ('missing-run', 'process coverage'),
                                             ('exit', 'retire'), ('scope', 'child result')])
def test_false_native_diagnostic_closure_is_refused(monkeypatch, damage, expected):
    original = readback.load

    def changed(path):
        value = original(path)
        if path == ROOT / 'owner.json':
            if damage == 'source':
                value['script_sha256'] = '0' * 64
            elif damage == 'missing-run':
                value['runs'].pop()
            elif damage == 'exit':
                value['runs'][0]['exit_code'] = 1
        if damage == 'scope' and path == ROOT / '0-parent-native-only/result.json':
            value['mode'] = 'resident'
        return value

    monkeypatch.setattr(readback, 'load', changed)
    with pytest.raises(ValueError, match=expected):
        readback.audit(ROOT)
