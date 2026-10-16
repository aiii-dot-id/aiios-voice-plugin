"""Endurance requires bounded period coverage, content and retained failure evidence."""
import io
import json
import queue
import struct
import sys
from types import SimpleNamespace

import pytest

from scripts.prove_native_meeting_endurance import (
    EventJournal, retain_observations, retire_owners, validate_observations,
    validate_registry_observations,
)
from scripts.prove_plugin_sdk_engine import SDKHost

TEXT = 'please keep the opening words cobalt lantern seventeen recovery is complete'


@pytest.mark.parametrize('damage', [None, 'track', 'uuid', 'provisional', 'authority'])
def test_registry_endurance_requires_exact_stable_solo_attribution(damage):
    finals = [dict(sequence=i, session_id='meeting', track_id=f'track-{i}',
                   start_sample=i*100, end_sample=i*100+20) for i in (1, 2)]
    observations = [dict(**e, refers_to=e['sequence'], speaker_uuid='a',
                         continuity='matched', used_for_permissions=False) for e in finals]
    if damage == 'track': observations[1]['track_id'] = 'stale'
    elif damage == 'uuid': observations[1]['speaker_uuid'] = 'b'
    elif damage == 'provisional': observations[1]['continuity'] = 'provisional'
    elif damage == 'authority': observations[1]['used_for_permissions'] = True
    if damage:
        with pytest.raises(AssertionError): validate_registry_observations(finals, observations)
    else:
        assert validate_registry_observations(finals, observations)['exact_segment_joins'] == 2


def final(sequence, start):
    return dict(type='transcript_final', sequence=sequence, start_sample=start,
                end_sample=start+10, text=TEXT)


def validate(finals, observations=None):
    return validate_observations(finals, observations if observations is not None else
                                 [dict(refers_to=e['sequence']) for e in finals],
                                 120, 10, 60, TEXT, pre_roll=2, tail=3)


def test_full_span_and_preroll_are_both_required():
    events = [final(1, 0), {**final(2, 60), 'start_sample': 58}]
    assert len(validate(events)) == 2
    with pytest.raises(AssertionError):
        validate(events[:1])


def test_actual_preroll_regression_geometry():
    events = [dict(sequence=1, start_sample=0, end_sample=160000, text=TEXT),
              dict(sequence=2, start_sample=943616, end_sample=1120000, text=TEXT)]
    assert len(validate_observations(events, [dict(refers_to=1), dict(refers_to=2)],
                                    1920000, 160000, 960000, TEXT)) == 2


@pytest.mark.parametrize('damage', [None, 'context_gap', 'context_overlap', 'lost_period', 'words'])
def test_continuous_context_is_not_claimed_as_word_alignment(damage):
    events = [dict(sequence=1, start_sample=0, end_sample=97792, text=TEXT),
              dict(sequence=2, start_sample=97792, end_sample=1056768, text=TEXT)]
    if damage == 'context_gap': events[1]['start_sample'] += 1
    elif damage == 'context_overlap': events[1]['start_sample'] -= 1
    elif damage == 'lost_period': events = [dict(sequence=1,start_sample=0,end_sample=1056768,text=TEXT)]
    elif damage == 'words': events[1]['text'] = 'stale wrong words'
    def check():
        return validate_observations(events, [dict(refers_to=e['sequence']) for e in events],
            1920000,112400,960000,TEXT,continuous_context=True)
    if damage:
        with pytest.raises(AssertionError): check()
    else:
        assert len(check()) == 2


@pytest.mark.parametrize('damage', ['missing_uid', 'duplicate_uid', 'duplicate_final',
                                   'one_long_final', 'wrong_words', 'empty_text',
                                   'stale_span', 'outside', 'too_much_preroll'])
def test_false_closure_is_refused(damage):
    events = [final(1, 0), final(2, 60)]
    observations = [dict(refers_to=1), dict(refers_to=2)]
    if damage == 'missing_uid': observations.pop()
    elif damage == 'duplicate_uid': observations.append(dict(refers_to=2))
    elif damage == 'duplicate_final': events.append(final(1, 60))
    elif damage == 'one_long_final':
        events = [{**final(1, 0), 'end_sample': 70}]; observations = [dict(refers_to=1)]
    elif damage == 'wrong_words': events[1]['text'] = 'unrelated stale speech'
    elif damage == 'empty_text': events[1]['text'] = ''
    elif damage == 'stale_span': events[1] = final(2, 0)
    elif damage == 'outside': events[1]['end_sample'] = 121
    elif damage == 'too_much_preroll': events[1]['start_sample'] = 57
    with pytest.raises(AssertionError): validate(events, observations)


def test_incremental_journal_survives_later_validator_failure(tmp_path):
    journal = EventJournal(tmp_path/'events.jsonl')
    event = final(1, 0)
    journal.append(event)
    assert json.loads((tmp_path/'events.jsonl').read_text()) == event
    with pytest.raises(AssertionError): validate([event])
    journal.close()
    assert json.loads((tmp_path/'events.jsonl').read_text()) == event


def test_event_sink_is_on_the_actual_reader(tmp_path):
    journal = EventJournal(tmp_path/'events.jsonl')
    body = json.dumps(dict(method='session.event', params=final(1, 0))).encode()
    host = SDKHost.__new__(SDKHost)
    host.process = SimpleNamespace(stdout=io.BytesIO(struct.pack('>I', len(body))+body))
    host.events, host.errors, host.started, host.event_sink = [], queue.Queue(), 0, journal.append
    host.control_reader()
    journal.close()
    assert host.errors.empty()
    assert json.loads((tmp_path/'events.jsonl').read_text()) == host.events[0]


def test_event_journal_failure_is_not_silently_a_pass():
    def fail(event): raise OSError('journal disk failure')
    body = json.dumps(dict(method='session.event', params=final(1, 0))).encode()
    host = SDKHost.__new__(SDKHost)
    host.process = SimpleNamespace(stdout=io.BytesIO(struct.pack('>I', len(body))+body))
    host.events, host.errors, host.started, host.event_sink = [], queue.Queue(), 0, fail
    host.control_reader()
    assert 'journal disk failure' in host.errors.get_nowait()


def test_cleanup_attempts_both_owners_and_retains_failure():
    called = []
    def fail(): called.append('host'); raise RuntimeError('retirement failed')
    host = SimpleNamespace(close=fail, process=SimpleNamespace(poll=lambda: None))
    broker = SimpleNamespace(close=lambda: called.append('broker'))
    report = dict(passed=True)
    retire_owners(report, host, broker)
    assert called == ['host', 'broker']
    assert not report['passed'] and not report['process_retired']
    assert 'retirement failed' in report['cleanup_errors'][0]


def test_terminal_failure_keeps_observations():
    host = SimpleNamespace(events=[final(1, 0), dict(type='speaker_observation', refers_to=1)],
                           errors=queue.Queue(), frames=[])
    report = {}
    retain_observations(report, host, None, 120)
    assert len(report['finals']) == len(report['speaker_observations']) == 1
    assert report['fed_samples'] == 120 and report['wall_seconds'] is None


def test_preflight_failure_is_preserved_without_launch(tmp_path, monkeypatch):
    from scripts import prove_native_meeting_endurance as module
    out = tmp_path/'attempt'
    # Test refusal persistence without launching POSIX I/O on any test host.
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='posix'))
    monkeypatch.setattr(sys, 'argv', ['proof', '--checkpoint', str(tmp_path),
        '--recorded-input', str(tmp_path/'missing.wav'), '--expected-text', TEXT,
        '--out', str(out)])
    def refuse(*args): raise ValueError('source binding differs')
    monkeypatch.setattr(module, 'normalize_pcm_wav', refuse)
    with pytest.raises(ValueError, match='source binding differs'):
        module.main()
    report = json.loads((out/'result.json').read_text())
    assert report['status'] == 'failed' and not report['passed']
    assert not report['process_started'] and report['wall_seconds'] is None
    assert not report['full_eight_hour_run'] and 'source binding differs' in report['error']
    assert (out/'events.jsonl').read_text() == ''
