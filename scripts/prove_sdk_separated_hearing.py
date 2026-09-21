"""Frozen native speaker-separated candidate through the real SDK/carrier.

Recorded microphone input, simulated host and sink. This does not qualify an
installed browser, persistent speaker UUIDs, enrolled identity, or live audio.
"""
import argparse
import json
import queue
import struct
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

from scripts.build_plugin_carrier import SDK_SOURCE
from scripts.native_checkpoint_binding import sha, verify_checkpoint
from scripts.plugin_receipt_probe import receipt
from scripts.prove_plugin_sdk_engine import SDKHost
from scripts.score_speaker_aware import evaluate
from runtime.plugin_engine.audio import Frame, PCM, END


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('checkpoint', 'panel', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--registry', action='store_true', help='Require resident durable UUIDs and post-session naming through the test host')
    args = parser.parse_args()
    out, checkpoint = args.out.resolve(), args.checkpoint.resolve()
    out.mkdir(parents=True, exist_ok=False)
    frozen, build, _, bindings = verify_checkpoint(checkpoint)
    if not frozen.get('hearing_replaced'):
        raise ValueError('explicit separated native checkpoint required')
    bindings[str(args.panel.resolve())] = sha(args.panel)
    bindings[str(Path(__file__).resolve())] = sha(__file__)
    panel = json.loads(args.panel.read_text())
    report = dict(passed=False, scope=__doc__, bindings=bindings, cases=[],
                  persistent_uid_qualified=False, installed=False, process_retired=False,
                  sdk_revision=build['sdk_revision'], checkpoint=str(checkpoint),
                  runtime_manifest_sha256=frozen['runtime_manifest_sha256'])
    host = None
    stopped = threading.Event()
    broker = None
    registry_broker = None
    errors = []
    hypotheses = {}
    started = time.monotonic()
    try:
        owner = out/'owner'
        owner.mkdir()
        carrier = checkpoint/'runtime'/('aii-voice-t3.exe' if frozen.get('platform') == 'windows' else 'aii-voice-t3')
        host_args = SimpleNamespace(output=owner, carrier=carrier, fixture=False,
            backend='native-common-'+frozen['backend'], stage=None, sdk_source=SDK_SOURCE,
            packaged_runtime=True, runtime_manifest_sha=frozen['runtime_manifest_sha256'],
            model_data_root=Path(frozen['models_root']),
            operator_settings={'tts_voice':'alba', 'turn_pause_ms':5000}, extra_host_operations=['fs.read','fs.write','fs.publish'] if args.registry else ['fs.read'])
        host = SDKHost(host_args)

        def absent_private_store():
            try:
                while not stopped.is_set():
                    try:
                        request = host.host_requests.get(timeout=.05)
                    except queue.Empty:
                        continue
                    params = request['params']
                    if (params['operation'] != 'fs.read' or params['target']['root'] != 'private'
                            or params['target']['path'] not in ('uid/enrollment.json', 'uid/captures.json')
                            or set(params['arguments']) != {'offset', 'length', 'digest'}):
                        raise ValueError('unexpected private-store operation')
                    raw = json.dumps(dict(jsonrpc='2.0', id=request['id'],
                        result=dict(status='failed', reasonCode='FS_NOT_FOUND'))).encode()
                    with host.write_lock:
                        host.process.stdin.write(struct.pack('>I', len(raw))+raw)
                        host.process.stdin.flush()
            except Exception as error:
                errors.append(repr(error))

        if args.registry:
            from scripts.speaker_registry_test_host import RegistryBroker, operation
            registry_broker = RegistryBroker(host)
            bindings[str(Path(__file__).with_name('speaker_registry_test_host.py'))] = sha(Path(__file__).with_name('speaker_registry_test_host.py'))
        else:
            broker = threading.Thread(target=absent_private_store)
            broker.start()
        report['ready'] = host.readiness()
        assert report['ready']['models_loaded'] == 5
        for index, case in enumerate(panel['cases']):
            sid = 'separated-'+str(index)
            path = args.panel.parent/case['audio_file']
            assert sha(path) == case['audio_sha256'], 'recording binding'
            bindings[str(path.resolve())] = sha(path)
            with wave.open(str(path), 'rb') as audio:
                assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()) == (1,2,16000,case['samples'])
                pcm = audio.readframes(audio.getnframes())
            opened = host.call('open', dict(session_id=sid, input_handle='mic', output_handle='speaker',
                audio=dict(format='s16le', input=dict(rate=16000, channels=1), output=dict(rate=24000, channels=1))))
            assert opened['audio']['input'] == dict(rate=16000, channels=1)
            host.event('session_ready', session_id=sid)
            generation = sid+'-interrupt'
            host.call('synthesize', dict(session_id=sid, synthesis_id=generation,
                text='This spoken reply continues until incoming speech interrupts it. '*12))
            event = host.event('synthesis_start', generation, session_id=sid)
            host.first_pcm(event['output_stream'], timeout=40)
            feed_start = time.monotonic()
            for sequence, offset in enumerate(range(0, case['samples'], 512), 1):
                # Exercise a Finish admitted before its audio tail arrives.
                if offset+512 >= case['samples']:
                    host.call('finish_input', dict(session_id=sid, stream_id='mic', end_sample=case['samples']))
                host.to_engine.write(Frame(PCM, 7, sequence, offset, pcm[offset*2:(offset+512)*2]).encode())
                time.sleep(max(0, min(offset+512, case['samples'])/16000-(time.monotonic()-feed_start)))
            host.to_engine.write(Frame(END, 7, sequence+1, case['samples']).encode())
            host.event('interruption_requested', generation, session_id=sid)
            cancelled = host.event('synthesis_cancelled', generation, timeout=30, session_id=sid)
            assert cancelled['delivered_samples'] > 0
            interruption = receipt(host, sid, cancelled, stopped=True)
            completed = host.event('input_finished', timeout=90, session_id=sid)
            assert completed['end_sample'] == completed['processed_end_sample'] == case['samples']
            finals = [e for e in host.events if e['session_id']==sid and e['type']=='transcript_final']
            assert finals and all(e['track_id'] and 0<=e['start_sample']<e['end_sample']<=case['samples'] for e in finals)
            assert len({e['track_id'] for e in finals}) == len(finals), 'case unexpectedly split or repeated a track'
            hypotheses[case['id']] = [dict(speaker=e['track_id'], start_time=e['start_sample']/16000,
                end_time=e['end_sample']/16000, words=e['text']) for e in finals]
            recovery = sid+'-recovery'
            host.call('synthesize', dict(session_id=sid, synthesis_id=recovery, text='Recovery is complete.'))
            finished = host.event('synthesis_end', recovery, timeout=60, session_id=sid)
            assert finished['delivered_samples'] > 0
            host.call('close', dict(session_id=sid, mode='drain'))
            assert host.call('status', dict(session_id=sid))['lifecycle'] == 'draining'
            recovered = receipt(host, sid, finished)
            terminal = host.event('session_end', session_id=sid)
            assert terminal['status'] == 'completed'
            observations = [e for e in host.events if e['session_id']==sid and e['type']=='speaker_observation']
            assert len(observations) == len(finals)
            by_sequence = {e['sequence']:e for e in finals}
            seen = set()
            for observation in observations:
                assert observation['refers_to'] not in seen
                seen.add(observation['refers_to'])
                final = by_sequence[observation['refers_to']]
                assert all(observation[key] == final[key] for key in ('track_id', 'start_sample', 'end_sample'))
                assert observation['decision'] == 'uncertain' and not observation['speaker_id']
                if args.registry:
                    import uuid
                    assert str(uuid.UUID(observation['speaker_uuid'])) == observation['speaker_uuid']
                    assert observation['continuity'] in ('matched','new_profile','provisional')
                    assert int(observation['registry_revision']) > 0
            assert not any(e['type']=='failure' for e in host.events)
            report['cases'].append(dict(case=case['id'], finals=finals, observations=observations,
                interruption=interruption, recovery=recovered, terminal=terminal))
        report['score'] = evaluate(panel, hypotheses)
        if args.registry:
            baseline={}
            for case in report['cases']:
                mapping={str(s['hypothesis_track']):s['reference_speaker'] for s in report['score']['cases'][case['case']]['speakers']}
                for observation in case['observations']:
                    speaker=mapping[observation['track_id']]
                    value=observation['speaker_uuid']
                    if case['case'] in ('solo_a','solo_b'):
                        assert observation['continuity']=='new_profile'
                        baseline[speaker]=value
                    elif observation['continuity']=='matched':
                        assert value==baseline[speaker], 'wrong persistent speaker'
                    else:
                        assert observation['continuity']=='provisional' and value not in baseline.values()
            assert len(baseline)==2 and len(set(baseline.values()))==2
            listed=operation(host,'speaker.buckets',{})
            assert listed['session_open'] is False
            selected=next(iter(baseline.values()))
            named=operation(host,'speaker.associate',dict(speaker_uuid=selected,
                registry_revision=listed['registry_revision'],display_label='Operator-selected label'),confirmed=True)
            assert any(r['speaker_uuid']==selected and r.get('display_label')=='Operator-selected label' for r in named['speakers'])
            report['registry']=dict(resident_uuid_path=True,post_session_naming=True,listing=named,
                                   test_host_only=True,process_restart_qualified=False)
            # Retire the actual carrier/worker, retaining only test-host storage.
            # Re-list and label from a fresh process without opening a mic.
            report['first_process_events']=host.events
            assert host.close()==0 and host.process.poll() is not None
            registry_broker.close()
            retained=registry_broker.storage
            host_args.output=out/'restarted-owner'
            host_args.output.mkdir()
            host=SDKHost(host_args)
            registry_broker=RegistryBroker(host,retained)
            host.readiness()
            reloaded=operation(host,'speaker.buckets',{})
            assert reloaded==named, 'registry projection changed across process restart'
            renamed=operation(host,'speaker.associate',dict(speaker_uuid=selected,
                registry_revision=reloaded['registry_revision'],display_label='Later label'),confirmed=True)
            assert any(r['speaker_uuid']==selected and r.get('display_label')=='Later label' for r in renamed['speakers'])
            report['registry']['process_restart_qualified']=True
            report['registry']['rename_after_restart']=renamed
            again=panel['cases'][0]
            assert again['id']=='solo_a', 'restart fixture must be the first enrolled acoustic speaker'
            with wave.open(str(args.panel.parent/again['audio_file']),'rb') as audio:
                pcm=audio.readframes(audio.getnframes())
            sid='restart-acoustic-match'
            host.call('open',dict(session_id=sid,input_handle='mic',output_handle='speaker',
                audio=dict(format='s16le',input=dict(rate=16000,channels=1),output=dict(rate=24000,channels=1))))
            host.event('session_ready',session_id=sid)
            begun=time.monotonic()
            for sequence,offset in enumerate(range(0,again['samples'],512),1):
                host.to_engine.write(Frame(PCM,7,sequence,offset,pcm[offset*2:(offset+512)*2]).encode())
                time.sleep(max(0,min(offset+512,again['samples'])/16000-(time.monotonic()-begun)))
            host.call('finish_input',dict(session_id=sid,stream_id='mic',end_sample=again['samples']))
            host.to_engine.write(Frame(END,7,sequence+1,again['samples']).encode())
            host.event('input_finished',timeout=90,session_id=sid)
            host.call('close',dict(session_id=sid,mode='drain'))
            assert host.event('session_end',session_id=sid)['status']=='completed'
            observations=[e for e in host.events if e['session_id']==sid and e['type']=='speaker_observation']
            assert len(observations)==1 and observations[0]['speaker_uuid']==selected
            assert observations[0]['continuity']=='matched' and observations[0]['display_label']=='Later label'
            report['registry']['acoustic_match_after_restart']=observations[0]
            forgotten=operation(host,'speaker.forget',dict(speaker_uuid=selected,
                registry_revision=renamed['registry_revision']),confirmed=True)
            assert all(r['speaker_uuid']!=selected for r in forgotten['speakers'])
            assert len(forgotten['speakers'])==len(renamed['speakers'])-1
            assert forgotten['session_open'] is False
            report['registry']['confirmed_forget_after_close']=True
        report['events'] = host.events
        assert report['score']['passed'], 'speaker-specific words did not meet the frozen panel gate'
        for path, digest in bindings.items():
            assert sha(path) == digest, 'input binding changed'
        report['passed'] = True
    except Exception as error:
        report['error'] = repr(error)
    finally:
        stopped.set()
        if broker is not None:
            broker.join(timeout=2)
            report['broker_retired'] = not broker.is_alive()
            report['passed'] &= report['broker_retired'] and not errors
        report['broker_errors'] = errors
        if registry_broker is not None:
            try:
                registry_broker.close()
                report['registry_broker_retired']=True
            except Exception as error:
                report['registry_broker_error']=repr(error)
                report['passed']=False
        if host is not None:
            report['events'] = host.events
            try:
                report['exit_code'] = host.close()
                report['process_retired'] = host.process.poll() is not None
                report['passed'] &= report['exit_code'] == 0 and report['process_retired']
            except Exception as error:
                report['cleanup_error'] = repr(error)
                report['passed'] = False
        report['elapsed_seconds'] = time.monotonic()-started
        (out/'result.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({k:report.get(k) for k in ('passed','error','process_retired','elapsed_seconds')}))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
