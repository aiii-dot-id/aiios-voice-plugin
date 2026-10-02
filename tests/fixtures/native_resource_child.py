# Frozen historical child for AST instrumentation regression, not a live gate.
# External runtime imports occur only inside child; tests parse, never execute it.
"""Paired TTS timing in the complete Windows five-model speech engine.

Recorded speech and a simulated sink; no physical audio or installed changes.
Keep the completed startup result separate from this additional TTS gate.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import statistics
import struct
import subprocess
import sys
import threading
import time
from types import SimpleNamespace


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def timing_gate(rows):
    assert [r['arm'] for r in rows] == ['baseline', 'candidate', 'candidate', 'baseline']
    assert all(len(r['speech']) == 4 for r in rows)
    means = {}
    for arm in ('baseline', 'candidate'):
        means[arm] = {}
        for position in range(4):
            group = [r['speech'][position] for r in rows if r['arm'] == arm]
            means[arm][str(position)] = {key: statistics.mean(r[key] for r in group)
                                        for key in ('first_pcm_ms', 'rtf')}
    ratios = {p: {k: means['candidate'][p][k] / means['baseline'][p][k]
                  for k in ('first_pcm_ms', 'rtf')} for p in means['baseline']}
    real_time = all(s['rtf'] <= 1 for r in rows if r['arm'] == 'candidate' for s in r['speech'])
    preserved = all(v <= 1.05 for g in ratios.values() for v in g.values())
    return {'means_by_repeat_and_reply': means, 'ratios': ratios,
            'all_candidate_replies_faster_than_real_time': real_time,
            'within_five_percent_each_position': preserved,
            'performance_gate_passed': real_time and preserved}


def speech_metrics(report):
    from scripts.audit_native_tts_pair import speech_metrics as checked
    return checked(report)


def child(source, output, profile, arm):
    from scripts.prove_plugin_sdk_engine import SDKHost, run
    from scripts.prove_native_uid_desktops import observe
    p = json.loads(profile.read_text())
    cfg = SimpleNamespace(output=output / 'owner', carrier=Path(p['carrier']), fixture=False,
                          backend='native-common-vulkan', stage=None, operator_settings={'turn_pause_ms': 768},
                          extra_host_operations=['fs.read'], sdk_source=source / 'plugin-sdk-placeholder',
                          spoken_interrupt=True, playback_reports=True)
    pin = json.loads((source / 'plugin/sdk-source.json').read_text())
    cfg.sdk_source = source / pin['source']
    cfg.output.mkdir()
    result = {'passed': False, 'arm': arm, 'speech': [], 'conversations': []}
    host = None
    stop = threading.Event()
    errors = []
    thread = None
    def save():
        (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    try:
        host = SDKHost(cfg, worker_command=[p['worker'], *p['model_paths'], p['backend'], p['uid_model'], p['policy']])
        result['readiness'] = host.readiness()
        assert result['readiness']['models_loaded'] == 5 and result['readiness']['accelerator'] == 'cpu_vulkan'
        result['loaded_worker'] = observe(host.process.pid, Path(p['worker']).parent, 'windows', p['libraries'])
        save()
        def broker():
            try:
                while not stop.is_set():
                    try:
                        request = host.host_requests.get(timeout=.05)
                    except queue.Empty:
                        continue
                    q = request['params']
                    assert q['operation'] == 'fs.read' and q['target'] == {'root': 'private', 'path': 'uid/enrollment.json'}
                    # No operator storage is read or changed in this timing test.
                    reply = {'jsonrpc': '2.0', 'id': request['id'],
                             'result': {'status': 'failed', 'reasonCode': 'FS_NOT_FOUND'}}
                    raw = json.dumps(reply).encode()
                    with host.write_lock:
                        host.process.stdin.write(struct.pack('>I', len(raw)) + raw)
                        host.process.stdin.flush()
            except Exception as error:
                errors.append(repr(error))
        thread = threading.Thread(target=broker)
        thread.start()
        for repeat in range(2):
            cfg.output = output / ('conversation-' + str(repeat))
            assert run(cfg, host=host, session_id='tts-pair-' + str(repeat),
                       synthesis_prefix='repeat-' + str(repeat) + '-', close_host=False)
            path = cfg.output / 'report.json'
            report = json.loads(path.read_text())
            result['speech'].extend(speech_metrics(report))
            result['conversations'].append({'path': str(path), 'sha256': sha(path)})
            save()
        assert not errors, errors
        stop.set()
        thread.join(2)
        assert not thread.is_alive()
        result['exit_code'] = host.close()
        host = None
        assert result['exit_code'] == 0
        result['passed'] = True
    finally:
        stop.set()
        if thread:
            thread.join(2)
        if host:
            result['cleanup_exit'] = host.close()
        result['broker_errors'] = errors
        save()
