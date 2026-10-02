"""Private in-memory host for recorded-speech SDK proofs, NOT installation proof.

This fixture simulates synced receipts. It cannot qualify the real host's disk
durability, containment, UI or speaker filters. No profile bytes are logged.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import queue
import re
import struct
import threading
import time


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def operation(host, name, args, *, confirmed=False):
    args = dict(args, _host_now_ms=time.time_ns()//1_000_000)
    if confirmed:
        args['_host_operator_act'] = dict(id='fixture-'+str(host.counter+1),
            confirmed_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
    host.counter += 1
    request = dict(jsonrpc='2.0', id=host.counter, method='invoke.call',
                   params=dict(operation=name, arguments=args))
    raw = json.dumps(request).encode()
    with host.write_lock:
        host.process.stdin.write(struct.pack('>I', len(raw))+raw)
        host.process.stdin.flush()
    reply = host.responses.get(timeout=45)
    if reply.get('id') != request['id'] or 'error' in reply:
        raise ValueError('speaker operation refused: '+json.dumps(reply))
    return reply['result']['operation_result']


class RegistryBroker:
    def __init__(self, host, storage=None):
        self.host = host
        self.storage = {} if storage is None else storage
        self.errors = []
        self.calls = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def answer(self, params):
        op, target, args = params['operation'], params['target'], params['arguments']
        name = target['path']
        assert target['root'] == 'private'
        regular = name in ('uid/speakers.json', 'uid/enrollment.json', 'uid/captures.json')
        stage = re.fullmatch(r'uid/\.speakers-[0-9a-f]{64}\.pending', name)
        assert regular or stage, 'unexpected private-store target'
        if op == 'fs.read':
            assert regular and set(args)=={'offset','length','digest'} and args['length']==65536
            if name not in self.storage:
                return dict(status='failed', reasonCode='FS_NOT_FOUND')
            raw = self.storage[name]
            offset = args['offset']
            assert 0 <= offset <= len(raw)
            data = raw[offset:offset+65536]
            value = dict(**target, offset=offset, size=len(raw), bytes=len(data),
                         eof=offset+len(data)==len(raw), data_b64=base64.b64encode(data).decode())
            if args['digest']:
                value['sha256'] = digest(raw)
        elif op == 'fs.write':
            assert stage and set(args)=={'data_b64','append'}
            raw = base64.b64decode(args['data_b64'], validate=True)
            assert 0 < len(raw) <= 65536
            self.storage[name] = (self.storage.get(name,b'') if args['append'] else b'')+raw
            assert len(self.storage[name]) <= 8<<20
            value = dict(**target, bytes=len(raw), size=len(self.storage[name]), appended=args['append'])
        elif op == 'fs.publish':
            assert name=='uid/speakers.json' and set(args) in (
                {'from','sha256','expected_absent'}, {'from','sha256','expected_sha256'})
            assert re.fullmatch(r'uid/\.speakers-[0-9a-f]{64}\.pending', args['from'])
            raw, before = self.storage[args['from']], self.storage.get(name)
            assert digest(raw)==args['sha256']
            conflict = before is not None if args.get('expected_absent') else before is None or digest(before)!=args['expected_sha256']
            if conflict:
                return dict(status='failed', reasonCode='FS_GENERATION_MISMATCH')
            self.storage[name] = raw
            del self.storage[args['from']]
            value = dict(**target, size=len(raw), sha256=digest(raw), replaced=before is not None,
                         durable=True, durability='synced')
        else:
            raise ValueError('unexpected host operation')
        self.calls.append(dict(operation=op, path=name, bytes=value.get('bytes'), sha256=value.get('sha256')))
        return dict(status='succeeded', operation_result=value)

    def run(self):
        try:
            while not self.stop.is_set():
                try:
                    request = self.host.host_requests.get(timeout=.05)
                except queue.Empty:
                    continue
                raw = json.dumps(dict(jsonrpc='2.0', id=request['id'], result=self.answer(request['params']))).encode()
                with self.host.write_lock:
                    self.host.process.stdin.write(struct.pack('>I',len(raw))+raw)
                    self.host.process.stdin.flush()
        except Exception as error:
            self.errors.append(repr(error))

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        assert not self.thread.is_alive() and not self.errors, self.errors
