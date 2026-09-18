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
from scripts.prove_plugin_sdk_engine import SDKHost
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


def validate_observations(finals, observations, total, recording_samples, period):
    assert finals and {e['sequence'] for e in finals} == {e['refers_to'] for e in observations}
    # One early final cannot certify hours of later dead inference.
    for start in range(0, total-recording_samples+1, period):
        assert any(start <= e['start_sample'] < start+recording_samples for e in finals), start


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--recorded-input', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--seconds', type=int, default=8*3600)
    p.add_argument('--period', type=int, default=60)
    a = p.parse_args()
    if os.name != 'posix':
        raise ValueError('this harness requires POSIX nonblocking pipe readiness')
    pcm = np.asarray(np.clip(normalize_pcm_wav(a.recorded_input, 16000).samples,
                             -1, 32767/32768)*32768, dtype='<i2').tobytes()
    if a.period < len(pcm)/32000 + 3 or a.seconds < len(pcm)/32000 + 3:
        raise ValueError('a full recording plus finalization silence is required')
    cp, out = a.checkpoint.resolve(), a.out.resolve()
    frozen, _, _, bindings = verify_checkpoint(cp)
    bindings[str(Path(__file__).resolve())] = sha(Path(__file__))
    bindings[str(a.recorded_input.resolve())] = sha(a.recorded_input)
    out.mkdir(parents=True, exist_ok=False)
    report = dict(passed=False, scope=__doc__, requested_seconds=a.seconds,
                  period_seconds=a.period, bindings=bindings, snapshots=[],
                  full_eight_hour_run=a.seconds >= 8*3600)
    host = broker = None
    started = None
    def save():
        (out/'result.json').write_text(json.dumps(report, indent=2)+'\n')
    try:
        os.environ['ORT_DISABLE_TELEMETRY'] = '1'
        cfg = SimpleNamespace(output=out/'owner', carrier=cp/'runtime/aii-voice-t3',
            fixture=False, backend='native-common-'+frozen['backend'], stage=None,
            sdk_source=SDK_SOURCE, packaged_runtime=True,
            runtime_manifest_sha=frozen['runtime_manifest_sha256'],
            model_data_root=Path(frozen['models_root']),
            operator_settings={'capture_limit_minutes': 0, 'turn_pause_ms': 768},
            extra_host_operations=['fs.read'])
        cfg.output.mkdir()
        host = SDKHost(cfg)
        broker = Broker(host, out/'private-fixture')
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
                assert not host.frames and not any(e['type'] == 'failure' for e in host.events)
                report['snapshots'].append(dict(wall_seconds=time.monotonic()-started,
                    fed_samples=position, state=state, processes=process_memory(host.process.pid)))
                report['max_source_lag_seconds'] = max_lag
                save()
                next_snapshot = position + 60*16000
        time.sleep(max(0, started+a.seconds-time.monotonic()))
        host.call('finish_input', dict(session_id=sid, stream_id='mic', end_sample=position))
        completion = host.event('input_finished', session_id=sid, timeout=30)
        assert completion['end_sample'] == total
        host.call('close', dict(session_id=sid, mode='drain'))
        host.event('session_end', session_id=sid, timeout=30)
        finals = [e for e in host.events if e['type'] == 'transcript_final']
        observations = [e for e in host.events if e['type'] == 'speaker_observation']
        validate_observations(finals, observations, total, len(pcm)//2, period)
        assert not host.frames and not any(e['type'] == 'failure' for e in host.events)
        assert all(c['operation'] == 'fs.read' for c in broker.calls)
        report.update(finals=finals, speaker_observations=observations,
                      completion=completion, wall_seconds=time.monotonic()-started,
                      fed_samples=position, max_source_lag_seconds=max_lag)
        broker.close(); broker = None
        report['exit_code'] = host.close(); host = None
        assert report['exit_code'] == 0
        report['passed'] = True
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        try:
            if host:
                host.close()
            if broker:
                broker.close()
        finally:
            save()
            print(json.dumps({k: report.get(k) for k in
                ('passed', 'full_eight_hour_run', 'wall_seconds', 'error')}), flush=True)


if __name__ == '__main__':
    main()
