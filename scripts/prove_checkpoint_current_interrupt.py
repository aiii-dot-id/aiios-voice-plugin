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
    parser.add_argument('--output-only', action='store_true', help='No microphone binding or fabricated Finish')
    parser.add_argument('--voice', default='alba', help='Exact preset accepted by the bound engine')
    parser.add_argument('--require-quiet-initializers', action='store_true')
    parser.add_argument('--require-attribution-containment', action='store_true',
                        help='Require explicit final attribution and no pooled person identity; not working diarized UID')
    parser.add_argument('--recorded-input', type=Path, required=True)
    a = parser.parse_args()
    if a.require_attribution_containment and (not a.recorded_conversation or a.output_only):
        parser.error('attribution containment requires a recorded input conversation')
    cp, out = a.checkpoint.resolve(), a.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    frozen, build, _, bindings = verify_checkpoint(cp)
    bindings[str(Path(__file__).resolve())] = sha(Path(__file__))
    report = dict(passed=False, scope=__doc__, bindings=bindings, cases=[],
                  sdk_revision=build['sdk_revision'], checkpoint=str(cp),
                  runtime_manifest_sha256=frozen['runtime_manifest_sha256'], output_only=a.output_only, voice=a.voice)
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
              model_data_root=Path(frozen['models_root']), operator_settings={'tts_voice': a.voice},
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
            opening = dict(session_id=sid, output_handle='speaker',
                audio=dict(format='s16le', input=None if a.output_only else dict(rate=16000, channels=1), output=dict(rate=24000, channels=1)))
            if not a.output_only:
                opening['input_handle'] = 'mic'
            opened = host.call('open', opening)
            assert 'input' in opened['audio']
            assert (opened['audio']['input'] is None) == a.output_only
            host.event('session_ready', session_id=sid)
            if a.output_only:
                status = host.call('status', dict(session_id=sid))
                assert status['input']['state'] == 'absent'
                assert status['recognition']['state'] == 'inactive'
                assert status['input_completion'] is None
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
            if not a.output_only:
                host.call('finish_input', dict(session_id=sid, stream_id='mic', end_sample=0))
                host.event('input_finished', session_id=sid)
            host.call('close', dict(session_id=sid, mode='drain'))
            host.event('session_end', session_id=sid)
            if a.output_only:
                assert not any(e['session_id'] == sid and (e['type'] == 'input_finished' or e['type'].startswith('transcript_')) for e in host.events)
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
        if a.require_attribution_containment:
            from jsonschema import Draft202012Validator
            schema_path = Path(__file__).resolve().parents[1] / 'spec/speaker_attribution.schema.json'
            bindings[str(schema_path)] = sha(schema_path)
            validator = Draft202012Validator(json.loads(schema_path.read_text()))
            finals = [e for e in host.events if e['type'] == 'transcript_final']
            amendments = [e for e in host.events if e['type'] == 'speaker_observation']
            assert finals and len(finals) == len(amendments), 'missing or duplicate attribution'
            keyed = {(e['session_id'], e['sequence']): e for e in finals}
            assert len(keyed) == len(finals), 'duplicate final'
            seen = set()
            for final in finals:
                validator.validate(final)
                assert final['attribution']['decision'] == 'pending'
                assert final['track_id'] == '', 'unseparated recognizer invented a track'
            for amendment in amendments:
                validator.validate(amendment)
                key = (amendment['session_id'], amendment['refers_to'])
                assert key in keyed and key not in seen, 'foreign or duplicate amendment'
                seen.add(key)
                final = keyed[key]
                assert amendment['sequence'] > final['sequence']
                assert all(amendment[k] == final[k] for k in ('track_id', 'start_sample', 'end_sample'))
                assert amendment['decision'] == 'uncertain', 'pooled identity escaped'
                assert not amendment['speaker'] and not amendment['speaker_id']
                assert 'native_evidence' not in amendment and 'evidence_scope' not in amendment
            report['attribution_containment'] = dict(passed=True, finals=len(finals),
                amendments=len(amendments), all_initial_pending=True, no_pooled_person_identity=True,
                scope='Real native models through SDK, no enrollment and simulated host; not diarized UID or installed consumer')
        introductions = {}
        for event in host.events:
            if event['type'] == 'synthesis_start':
                assert event['session_id'] and event['synthesis_id']
                assert event['output_stream'] not in introductions, 'process output stream reused'
                introductions[event['output_stream']] = event
        terminal_streams = set()
        for _, frame in host.frames:
            assert frame.stream in introductions, 'unnamed engine audio'
            assert frame.stream not in terminal_streams, 'engine audio after END'
            if frame.kind == 3:
                terminal_streams.add(frame.stream)
        assert terminal_streams == set(introductions), 'introduced stream never ended'
        report['producer_contract'] = dict(session_tagged=True, unique_streams=len(introductions),
                                          every_stream_named=True, no_audio_after_end=True)
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
