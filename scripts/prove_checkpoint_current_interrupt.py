"""Real five-model SDK current-generation interruption; simulated playback receipts.

Not browser, installed identity, physical audio, delayed-ack or release proof.
Startup spans are fresh-process measurements with existing operating-system caches.
"""
import argparse
import json
import os
from pathlib import Path
import queue
import struct
import threading
import time
from types import SimpleNamespace

from scripts.build_plugin_carrier import SDK_SOURCE
from scripts.native_checkpoint_binding import sha, verify_checkpoint
from scripts.plugin_receipt_probe import receipt
from scripts.prove_plugin_sdk_engine import SDKHost, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--recorded-conversation', action='store_true')
    parser.add_argument('--require-quiet-initializers', action='store_true')
    parser.add_argument('--recorded-input', type=Path, required=True)
    a = parser.parse_args()
    cp, out = a.checkpoint.resolve(), a.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    frozen, build, _, bindings = verify_checkpoint(cp)
    bindings[str(Path(__file__).resolve())] = sha(Path(__file__))
    report = dict(passed=False, scope=__doc__, bindings=bindings, cases=[],
                  sdk_revision=build['sdk_revision'], checkpoint=str(cp),
                  runtime_manifest_sha256=frozen['runtime_manifest_sha256'])
    host = None
    stopped = threading.Event()
    broker_thread = None
    broker_errors = []
    def save():
        (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    try:
        os.environ['AII_VOICE_STARTUP_TRACE'] = '1'
        owner = out / 'owner'
        owner.mkdir()
        carrier = cp / 'runtime' / ('aii-voice-t3.exe' if frozen.get('platform') == 'windows' else 'aii-voice-t3')
        cfg = SimpleNamespace(output=owner, carrier=carrier, fixture=False,
              backend='native-common-' + frozen['backend'], stage=None,
              sdk_source=SDK_SOURCE, packaged_runtime=True,
              runtime_manifest_sha=frozen['runtime_manifest_sha256'],
              model_data_root=Path(frozen['models_root']), operator_settings={},
              extra_host_operations=['fs.read'], recorded_input=a.recorded_input.resolve())
        host = SDKHost(cfg)
        # This isolated identity has no enrollment or retained captures. Answer
        # only the two real fixed-path reads with the host's typed absence.
        # Unexpected host operations remain failures, never generic success.
        def broker():
            try:
                while not stopped.is_set():
                    try:
                        request = host.host_requests.get(timeout=.05)
                    except queue.Empty:
                        continue
                    params = request['params']
                    assert params['operation'] == 'fs.read'
                    assert params['target']['root'] == 'private'
                    assert params['target']['path'] in ('uid/enrollment.json', 'uid/captures.json')
                    assert set(params['arguments']) == {'offset', 'length', 'digest'}
                    assert params['arguments']['length'] == 65536
                    raw = json.dumps(dict(jsonrpc='2.0', id=request['id'],
                        result=dict(status='failed', reasonCode='FS_NOT_FOUND'))).encode()
                    with host.write_lock:
                        host.process.stdin.write(struct.pack('>I', len(raw)) + raw)
                        host.process.stdin.flush()
            except Exception as error:
                broker_errors.append(repr(error))
        broker_thread = threading.Thread(target=broker)
        broker_thread.start()
        report['ready'] = host.readiness()
        assert report['ready']['models_loaded'] == 5
        for index, selector in enumerate(({'synthesis_id': ''}, {}, {'synthesis_id': None})):
            sid, generation = 'current-' + str(index), 'reply-' + str(index)
            host.call('open', dict(session_id=sid, input_handle='mic', output_handle='speaker',
                audio=dict(format='s16le', input=dict(rate=16000, channels=1), output=dict(rate=24000, channels=1))))
            host.event('session_ready', session_id=sid)
            host.call('synthesize', dict(session_id=sid, synthesis_id=generation,
                text='This is a long reply so the current generation remains active while we interrupt it. ' * 12))
            started = host.event('synthesis_start', generation, session_id=sid)
            host.first_pcm(started['output_stream'], timeout=40)
            began = time.perf_counter()
            stop = host.call('stop_playback', dict(session_id=sid, **selector))
            stopped_seconds = time.perf_counter() - began
            began = time.perf_counter()
            cancel = host.call('cancel_synthesis', dict(session_id=sid, **selector))
            cancelled_seconds = time.perf_counter() - began
            assert stop['synthesis_id'] == cancel['synthesis_id'] == generation
            terminal = host.event('synthesis_cancelled', generation, session_id=sid, timeout=20)
            observation = receipt(host, sid, terminal, stopped=True)
            assert stopped_seconds < 1 and cancelled_seconds < 1
            recovery = generation + '-recovery'
            host.call('synthesize', dict(session_id=sid, synthesis_id=recovery, text='Recovery is ready.'))
            recovered = host.event('synthesis_end', recovery, session_id=sid, timeout=40)
            recovered_receipt = receipt(host, sid, recovered)
            host.call('finish_input', dict(session_id=sid, stream_id='mic', end_sample=0))
            host.event('input_finished', session_id=sid)
            host.call('close', dict(session_id=sid, mode='drain'))
            host.event('session_end', session_id=sid)
            report['cases'].append(dict(selector=selector, stop_seconds=stopped_seconds,
                cancel_seconds=cancelled_seconds, interrupted=observation, recovery=recovered_receipt))
            save()
        assert not any(e['type'] == 'failure' for e in host.events)
        if a.recorded_conversation:
            conversation = SimpleNamespace(**{**vars(cfg), 'output': out / 'recorded-conversation',
                'spoken_interrupt': True, 'playback_reports': True})
            assert run(conversation, host=host, session_id='recorded-recovery',
                       synthesis_prefix='recorded-', close_host=False)
            report['recorded_conversation'] = str(conversation.output / 'report.json')
        report['events'], report['calls'] = host.events, host.calls
        stopped.set()
        broker_thread.join(timeout=2)
        assert not broker_thread.is_alive() and not broker_errors, broker_errors
        report['exit_code'] = host.close()
        host = None
        assert report['exit_code'] == 0
        stderr = (owner / 'worker.stderr.log').read_text(errors='replace')
        report['startup_phases'] = [json.loads(line) for line in stderr.splitlines()
            if line.startswith('{') and 'startup-profile' in line]
        report['unused_initializer_warnings'] = [line for line in stderr.splitlines()
            if 'CleanUnusedInitializersAndNodeArgs' in line]
        if a.require_quiet_initializers:
            assert report['unused_initializer_warnings'] == []
        for path, digest in bindings.items():
            assert sha(path) == digest, path
        report['passed'] = True
    finally:
        stopped.set()
        if broker_thread is not None:
            broker_thread.join(timeout=2)
            report['broker_retired'] = not broker_thread.is_alive()
            report['broker_errors'] = broker_errors
        if host is not None:
            report['cleanup_exit'] = host.close()
        save()
    print(json.dumps({k: report[k] for k in ('passed', 'ready', 'startup_phases', 'unused_initializer_warnings')}))


if __name__ == '__main__':
    main()
