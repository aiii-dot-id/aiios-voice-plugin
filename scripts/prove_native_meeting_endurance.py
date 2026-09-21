"""Wall-clock POSIX native meeting endurance, recorded speech and simulated host.

No microphone, AII OS browser, speaker authentication, or installed-product claim.
The engine is not asked to synthesize. Its zero capture limit must preserve
recognition and speaker observations throughout the explicitly requested run.
"""
import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import time
from types import SimpleNamespace

import numpy as np

from scripts.audio_contract import normalize_pcm_wav
from scripts.build_plugin_carrier import SDK_SOURCE
from scripts.native_checkpoint_binding import sha, verify_checkpoint
from scripts.prove_guided_capture_sdk import Broker
from scripts.speaker_registry_test_host import RegistryBroker
from scripts.prove_plugin_sdk_engine import SDKHost
from scripts.score_speaker_aware import distance, words
from runtime.plugin_engine.audio import Frame, PCM


def write_frame(fd, frame):
    pending = memoryview(frame.encode())
    deadline = time.monotonic() + 10
    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
            raise TimeoutError('meeting input pipe stalled')
        try:
            sent = os.write(fd, pending)
        except BlockingIOError:
            continue
        if sent <= 0:
            raise OSError('meeting input write made no progress')
        pending = pending[sent:]


def process_memory(root):
    rows = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,rss=,time='], text=True)
    table = [line.split() for line in rows.splitlines()]
    owned = {root}
    while True:
        children = {int(r[0]) for r in table if int(r[1]) in owned}
        if children <= owned:
            break
        owned |= children
    return [dict(pid=int(r[0]), rss_kib=int(r[2]), cpu_time=r[3])
            for r in table if int(r[0]) in owned]


def validate_observations(finals, observations, total, recording_samples, period,
                          expected_text, *, pre_roll=32*512, tail=3*16000,
                          continuous_context=False):
    """Associate each final once, tolerating bounded context but not lost speech."""
    reference = words(expected_text)
    if not reference or not 0 < recording_samples < period or total < recording_samples:
        raise ValueError('complete recording, reference text and separated periods required')
    seqs = [e['sequence'] for e in finals]
    refs = [e['refers_to'] for e in observations]
    if not seqs or len(set(seqs)) != len(seqs) or len(set(refs)) != len(refs) or set(seqs) != set(refs):
        raise AssertionError('missing or duplicate finals/speaker observations')
    groups = {start: [] for start in range(0, total-recording_samples+1, period)}
    previous_end = 0
    for e in sorted(finals, key=lambda event: event['end_sample']):
        lo, hi = e['start_sample'], e['end_sample']
        if type(lo) is not int or type(hi) is not int or not 0 <= lo < hi <= total:
            raise AssertionError('final outside source clock')
        if continuous_context:
            # The integrated recognizer retains silence since its prior final.
            # Its extent names consumed context, explicitly not word alignment.
            # Require non-overlapping context and an end inside exactly one
            # recording/tail window; content scoring still catches dropped or
            # duplicated words. Never accept one final covering two periods.
            if lo != previous_end:
                raise AssertionError('continuous context has a gap or overlap')
            matches = [s for s in groups if s < hi <= s+recording_samples+tail]
            previous_end = hi
        else:
            matches = [s for s in groups if lo < s+recording_samples and hi > s]
        if len(matches) != 1:
            raise AssertionError('final spans multiple periods or misses every recording')
        start = matches[0]
        if ((not continuous_context and lo < max(0, start-pre_roll))
                or hi > min(total, start+recording_samples+tail)):
            raise AssertionError('final exceeds bounded pre-roll/tail')
        groups[start].append(e)
    coverage = []
    for start, events in groups.items():
        hypothesis = words(' '.join(e['text'] for e in sorted(events, key=lambda e: e['start_sample'])))
        errors = distance(reference, hypothesis)
        if not events or errors / len(reference) > .20:
            raise AssertionError(f'speech coverage/content failed at {start}: {errors}/{len(reference)} word edits')
        coverage.append(dict(start_sample=start, finals=[e['sequence'] for e in events],
                             word_errors=errors, reference_words=len(reference)))
    return coverage


class EventJournal:
    """Persist each received event before any acceptance assertion can fail."""
    def __init__(self, path):
        self.stream = Path(path).open('x', encoding='utf-8')

    def append(self, event):
        self.stream.write(json.dumps(event, allow_nan=False)+'\n')
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self):
        self.stream.close()


def retain_observations(report, host, started, position):
    report.update(fed_samples=position, wall_seconds=None if started is None else time.monotonic()-started)
    if host is not None:
        report['finals'] = [e for e in host.events if e['type'] == 'transcript_final']
        report['speaker_observations'] = [e for e in host.events if e['type'] == 'speaker_observation']
        report['output_frame_count'] = len(host.frames)
        report['failure_events'] = [e for e in host.events if e['type'] == 'failure']
        with host.errors.mutex:
            report['reader_errors'] = list(host.errors.queue)


def retire_owners(report, host, broker):
    errors = []
    # Keep the broker serving until the child's control lane has retired.
    if host is not None:
        try:
            report['exit_code'] = host.close()
        except BaseException as error:
            errors.append('host: '+repr(error))
        report['process_retired'] = host.process is None or host.process.poll() is not None
    if broker is not None:
        try:
            broker.close()
        except BaseException as error:
            errors.append('broker: '+repr(error))
    report['cleanup_errors'] = errors
    if errors or report.get('exit_code') != 0 or not report.get('process_retired'):
        report['passed'] = False


def validate_registry_observations(finals, observations):
    """A solo endurance recording must retain one acoustic UUID, not just count events."""
    by_sequence = {event['sequence']: event for event in finals}
    identities = set()
    for event in observations:
        final = by_sequence[event['refers_to']]
        assert all(event.get(key) == final.get(key) for key in
                   ('session_id', 'track_id', 'start_sample', 'end_sample'))
        assert final.get('track_id') and event.get('speaker_uuid')
        assert event.get('continuity') in ('new_profile', 'matched')
        assert event.get('used_for_permissions') is False
        identities.add(event['speaker_uuid'])
    assert len(identities) == 1, 'solo speaker UUID did not remain stable'
    return dict(stable_uuid_count=len(identities), exact_segment_joins=len(observations))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--recorded-input', type=Path, required=True)
    p.add_argument('--expected-text', required=True, help='Ground truth for each repeated recording, scoring only')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--seconds', type=int, default=8*3600)
    p.add_argument('--period', type=int, default=60)
    p.add_argument('--registry', action='store_true', help='Require the integrated anonymous UUID registry')
    a = p.parse_args()
    if os.name != 'posix':
        raise ValueError('this harness requires POSIX nonblocking pipe readiness')
    cp, out = a.checkpoint.resolve(), a.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = dict(passed=False, scope=__doc__, requested_seconds=a.seconds,
                  period_seconds=a.period, bindings={}, snapshots=[],
                  requested_eight_hours=a.seconds >= 8*3600, full_eight_hour_run=False,
                  expected_text=a.expected_text, status='preflight', process_started=False,
                  process_retired=False, resident_registry=a.registry)
    host = broker = None
    started, position = None, 0
    journal = EventJournal(out/'events.jsonl')
    def save():
        temporary = out/'result.json.pending'
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        temporary.replace(out/'result.json')
    save()
    try:
        pcm = np.asarray(np.clip(normalize_pcm_wav(a.recorded_input, 16000).samples,
                                 -1, 32767/32768)*32768, dtype='<i2').tobytes()
        if not words(a.expected_text) or a.period < len(pcm)/32000 + 3 or a.seconds < len(pcm)/32000 + 3:
            raise ValueError('a full recording plus finalization silence is required')
        frozen, _, _, bindings = verify_checkpoint(cp)
        bindings[str(Path(__file__).resolve())] = sha(Path(__file__))
        for name in ('prove_plugin_sdk_engine.py', 'prove_guided_capture_sdk.py',
                     'score_speaker_aware.py', 'native_checkpoint_binding.py',
                     'speaker_registry_test_host.py'):
            path = Path(__file__).with_name(name)
            bindings[str(path.resolve())] = sha(path)
        bindings[str(a.recorded_input.resolve())] = sha(a.recorded_input)
        report.update(bindings=bindings, status='loading')
        save()
        os.environ['ORT_DISABLE_TELEMETRY'] = '1'
        cfg = SimpleNamespace(output=out/'owner', carrier=cp/'runtime/aii-voice-t3',
            fixture=False, backend='native-common-'+frozen['backend'], stage=None,
            sdk_source=SDK_SOURCE, packaged_runtime=True,
            runtime_manifest_sha=frozen['runtime_manifest_sha256'],
            model_data_root=Path(frozen['models_root']),
            operator_settings={'capture_limit_minutes': 0, 'turn_pause_ms': 768},
            extra_host_operations=['fs.read', 'fs.write', 'fs.publish'] if a.registry else ['fs.read'],
            event_sink=journal.append)
        cfg.output.mkdir()
        host = SDKHost(cfg)
        report['process_started'] = True
        broker = RegistryBroker(host) if a.registry else Broker(host, out/'private-fixture')
        report['ready'] = host.readiness()
        sid = 'meeting-endurance'
        host.call('open', dict(session_id=sid, mode='meeting', input_handle='mic',
            output_handle='speaker', audio=dict(format='s16le',
            input=dict(rate=16000, channels=1), output=dict(rate=24000, channels=1))))
        host.event('session_ready', session_id=sid)
        fd = host.to_engine.fileno()
        os.set_blocking(fd, False)
        total, period, packet = a.seconds*16000, a.period*16000, 512
        started, position, seq, next_snapshot = time.monotonic(), 0, 0, 0
        report['status'] = 'running'
        max_lag = 0
        while position < total:
            count = min(packet, total-position, period-position % period)
            offset = (position % period)*2
            data = pcm[offset:offset+count*2].ljust(count*2, b'\0')
            due = started + position/16000
            time.sleep(max(0, due-time.monotonic()))
            max_lag = max(max_lag, time.monotonic()-due)
            if max_lag > 10:
                raise TimeoutError('source could not sustain wall-clock capture')
            seq += 1
            write_frame(fd, Frame(PCM, 1, seq, position, data))
            position += count
            if position >= next_snapshot:
                state = host.call('status', dict(session_id=sid))
                assert state['lifecycle'] == 'open' and not state['reason'], state
                assert state['input']['state'] == 'accepting' and state['input_completion'] is None
                assert state['operator_settings']['capture_limit_minutes'] == 0
                assert state['bookkeeping']['unresolved_generations'] == 0
                assert state['bookkeeping']['identity_fences'] == 0
                assert state['bookkeeping']['session_fences'] <= 1
                assert len(state.get('attributions', [])) <= 128
                if a.registry:
                    assert len(broker.storage) <= 3 and sum(map(len, broker.storage.values())) <= 16<<20
                assert not host.frames and not any(e['type'] == 'failure' for e in host.events)
                report['snapshots'].append(dict(wall_seconds=time.monotonic()-started,
                    fed_samples=position, state=state, processes=process_memory(host.process.pid)))
                report['max_source_lag_seconds'] = max_lag
                retain_observations(report, host, started, position)
                save()
                next_snapshot = position + 60*16000
        time.sleep(max(0, started+a.seconds-time.monotonic()))
        host.call('finish_input', dict(session_id=sid, stream_id='mic', end_sample=position))
        completion = host.event('input_finished', session_id=sid, timeout=30)
        report['completion'] = completion
        assert completion['end_sample'] == total
        host.call('close', dict(session_id=sid, mode='drain'))
        report['session_end'] = host.event('session_end', session_id=sid, timeout=30)
        retain_observations(report, host, started, position)
        save()
        finals = [e for e in host.events if e['type'] == 'transcript_final']
        observations = [e for e in host.events if e['type'] == 'speaker_observation']
        report['coverage'] = validate_observations(finals, observations, total, len(pcm)//2,
                                                   period, a.expected_text,
                                                   continuous_context=a.registry)
        assert not host.frames and not any(e['type'] == 'failure' for e in host.events)
        if a.registry:
            report['registry'] = validate_registry_observations(finals, observations)
            report['registry']['stored_bytes'] = sum(map(len, broker.storage.values()))
        else:
            assert all(c['operation'] == 'fs.read' for c in broker.calls)
        report.update(finals=finals, speaker_observations=observations,
                      completion=completion, wall_seconds=time.monotonic()-started,
                      fed_samples=position, max_source_lag_seconds=max_lag)
        report['passed'] = True
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        try:
            retain_observations(report, host, started, position)
            save()
        except BaseException as error:
            report['passed'] = False
            report['persistence_error'] = repr(error)
        finally:
            retire_owners(report, host, broker)
            retain_observations(report, host, started, position)
            if report.get('reader_errors') or report.get('failure_events') or report.get('output_frame_count'):
                report['passed'] = False
            journal.close()
            report['event_journal_sha256'] = sha(out/'events.jsonl')
            report['status'] = 'passed' if report['passed'] else 'failed'
            report['full_eight_hour_run'] = bool(report['passed'] and a.seconds >= 8*3600
                                                and report['wall_seconds'] >= a.seconds)
            save()
            print(json.dumps({k: report.get(k) for k in
                ('passed', 'full_eight_hour_run', 'wall_seconds', 'error')}), flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
