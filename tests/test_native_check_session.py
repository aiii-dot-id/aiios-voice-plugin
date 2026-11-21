import base64
import hashlib
import json
import os
import queue
import struct
import subprocess
import threading
import time

import pytest

WORKER = os.environ.get('AII_NATIVE_INTERRUPT_FIXTURE') or os.environ.get('AII_TEST_WAVEFORM_WORKER')
pytestmark = pytest.mark.skipif(not WORKER, reason='needs the fixture worker (AII_NATIVE_INTERRUPT_FIXTURE)')
AUDIO = {'format': 's16le', 'input': {'rate': 16000, 'channels': 1}, 'output': {'rate': 24000, 'channels': 1}}
AFTER_CHECK = 'no recording after a check until a real session opens'
PCM, END = 1, 3


def frame(kind, stream, seq, start, payload=b''):
    return struct.pack('>4sB3xIIQI', b'AUD1', kind, stream, seq, start, len(payload)) + payload


class Engine:
    def __init__(self):
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        env = {**os.environ, 'AII_AUDIO_IN_FD': str(input_read), 'AII_AUDIO_OUT_FD': str(output_write)}
        self.p = subprocess.Popen([WORKER, *['fixture-path'] * 7], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env, pass_fds=(input_read, output_write), bufsize=0)
        os.close(input_read)
        os.close(output_write)
        self.audio = os.fdopen(input_write, 'wb', buffering=0)
        self.output = os.fdopen(output_read, 'rb', buffering=0)
        self.replies, self.events, self.errors = queue.Queue(), queue.Queue(), []
        self.lock = threading.Lock()
        self.storage = {}
        self.request = 0
        self.streams = 100
        threading.Thread(target=self.reader, daemon=True).start()

    def send(self, message):
        with self.lock:
            self.p.stdin.write((json.dumps(message, separators=(',', ':')) + '\n').encode())
            self.p.stdin.flush()

    def broker(self, q):
        ident = q['resource'].split(':', 1)[1]
        target, stage = 'recordings/' + ident + '.wav', 'recordings/.' + q.get('upload', '') + '.pending'
        action = q.get('action', 'read')
        if action == 'stage':
            self.storage[stage] = self.storage.get(stage, b'') + base64.b64decode(q['data_b64'], validate=True)
            result = {'root': 'private', 'path': stage, 'bytes': len(self.storage[stage]), 'size': len(self.storage[stage])}
        elif action == 'publish':
            self.storage[target] = self.storage.pop(stage)
            result = {'root': 'private', 'path': target, 'size': len(self.storage[target]),
                      'sha256': hashlib.sha256(self.storage[target]).hexdigest(), 'replaced': False,
                      'durable': True, 'durability': 'synced'}
        else:
            raw = self.storage[target]
            part = raw[q['offset']:q['offset'] + 65536]
            result = {'root': 'private', 'path': target, 'offset': q['offset'], 'size': len(raw), 'bytes': len(part),
                      'eof': q['offset'] + len(part) == len(raw), 'data_b64': base64.b64encode(part).decode()}
            if q['digest']:
                result['sha256'] = hashlib.sha256(raw).hexdigest()
        self.send({'snapshot_reply': {'id': q['id'], 'session_id': q['session_id'], 'value': result}})

    def reader(self):
        try:
            for line in self.p.stdout:
                message = json.loads(line)
                if 'snapshot_request' in message:
                    self.broker(message['snapshot_request'])
                elif 'settings_request' in message:
                    q = message['settings_request']
                    self.send({'settings_reply': {'id': q['id'], 'session_id': q['session_id'], 'values': {}}})
                elif 'event' in message:
                    self.events.put(message['event'])
                elif 'id' in message:
                    self.replies.put(message)
        except BaseException as exc:
            self.errors.append(exc)

    def call(self, operation, arguments=None, *, refused=None):
        self.request += 1
        self.send({'id': self.request, 'operation': operation, 'arguments': arguments or {}})
        reply = self.replies.get(timeout=10)
        assert reply['id'] == self.request, reply
        if refused is None:
            assert 'error' not in reply, reply
            return reply['result']
        assert 'error' in reply and refused in reply['error'], reply
        return reply['error']

    def event(self, kind):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            try:
                row = self.events.get(timeout=.1)
            except queue.Empty:
                continue
            if row['type'] == kind:
                return row
            assert row['type'] != 'failure' or kind == 'failure', row
        raise TimeoutError(kind)

    def session(self, sid, value, samples=16000, check=None):
        self.streams += 1
        stream = self.streams
        arguments = {'session_id': sid, 'input_handle': sid + '-in', 'output_handle': sid + '-out',
                     'audio': {**AUDIO, 'input': {**AUDIO['input'], 'stream': stream}}}
        if check is not None:
            arguments['check'] = check
        self.call('speech.session.open', arguments)
        self.event('session_ready')
        position, seq = 0, 0
        while position < samples:
            count = min(1024, samples - position)
            self.audio.write(frame(PCM, stream, seq, position, struct.pack('<h', value) * count))
            position, seq = position + count, seq + 1
        self.call('speech.session.finish_input', {'session_id': sid, 'stream_id': sid + '-in', 'end_sample': position})
        self.audio.write(frame(END, stream, seq, position))
        self.event('input_finished')
        self.call('speech.session.close', {'session_id': sid, 'mode': 'drain'})
        self.event('session_end')

    def close(self):
        self.audio.close()
        self.p.stdin.close()
        try:
            self.p.wait(timeout=7)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait(timeout=3)
        self.output.close()
        assert not self.errors, self.errors
        self.p.stderr.close()


@pytest.fixture
def engine():
    e = Engine()
    try:
        yield e
    finally:
        e.close()


def test_a_check_leaves_no_recording_and_refuses_recording_until_a_real_session(engine):
    engine.session('check-1', 777, check=True)
    engine.call('recording.record', refused=AFTER_CHECK)
    engine.call('recording.status', refused=AFTER_CHECK)
    assert not engine.storage, 'a check wrote to storage'


def test_a_real_session_after_a_check_records_its_own_audio_only(engine):
    engine.session('real-1', 1234)
    engine.session('check-1', 777, check=True)
    engine.call('recording.record', refused=AFTER_CHECK)
    engine.session('real-2', 2222)
    saved = engine.call('recording.record')['operation_result']
    assert saved['state'] == 'saved', saved
    wav = engine.storage[saved['private_path']]
    samples = struct.unpack('<%dh' % ((len(wav) - 44) // 2), wav[44:])
    assert set(samples) == {2222}, 'the recording after a check is not the real session\'s own audio alone'


def test_check_is_a_boolean_and_a_real_session_needs_none(engine):
    engine.call('speech.session.open', {'session_id': 'x-1', 'input_handle': 'x-1-in', 'output_handle': 'x-1-out',
                                        'audio': AUDIO, 'check': 'yes'}, refused='check must be true or false')
    engine.session('real-1', 1234, check=False)
    assert engine.call('recording.record')['operation_result']['state'] == 'saved'
