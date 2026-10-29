from scripts._assertions import require_assertions
require_assertions()
import argparse
import base64
import hashlib
import json
import os
import platform
import queue
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

from runtime.plugin_engine.audio import END, PCM, Frame
from scripts.runtime_limits import default_limits, worker_environment

TEXT = ("The quick brown fox jumps over the lazy dog. "
        "Shared weights let several voices speak at once, each with its own state.")
VALUES = {'tts_voice': 'alba', 'tts_seed': 20261009, 'turn_pause_ms': 768}


def memory(pid):
    out = {}
    if platform.system() == 'Darwin':
        text = subprocess.run(['footprint', str(pid)], capture_output=True, text=True).stdout
        for line in text.splitlines():
            line = line.strip()
            for key in ('phys_footprint:', 'phys_footprint_peak:'):
                if line.startswith(key):
                    value, unit = line[len(key):].split()
                    out[key[:-1] + '_mib'] = round(float(value) / (1024 if unit == 'KB' else 1), 1)
    if platform.system() != 'Windows':
        out['rss_mib'] = round(int(subprocess.run(['ps', '-o', 'rss=', '-p', str(pid)], capture_output=True,
                                                  text=True).stdout.strip() or 0) / 1024, 1)
    if platform.system() == 'Windows':
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (n, ctypes.c_size_t) for n in ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                                               'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage',
                                               'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]
        process = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0010, False, pid)
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if process and ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            out['private_mib'] = round(counters.PrivateUsage / 1048576, 1)
            out['working_set_mib'] = round(counters.WorkingSetSize / 1048576, 1)
        if process:
            ctypes.windll.kernel32.CloseHandle(process)
    if platform.system() == 'Linux':
        rows = subprocess.run(['nvidia-smi', '--query-compute-apps=pid,used_memory', '--format=csv,noheader,nounits'],
                              capture_output=True, text=True).stdout
        for row in rows.splitlines():
            p, used = [x.strip() for x in row.split(',')]
            if p == str(pid):
                out['device_mib'] = int(used)
    return out


def resample_24k_to_16k(pcm):
    samples = struct.unpack('<%dh' % (len(pcm) // 2), pcm)
    n = len(samples) * 2 // 3
    out = []
    for i in range(n):
        x = i * 1.5
        j = int(x)
        f = x - j
        b = samples[j + 1] if j + 1 < len(samples) else samples[j]
        out.append(int(round(samples[j] * (1 - f) + b * f)))
    return struct.pack('<%dh' % len(out), *out)


def inheritable(handles):
    for handle in handles:
        os.set_handle_inheritable(handle, True)
    startup = subprocess.STARTUPINFO()
    startup.lpAttributeList = {'handle_list': list(handles)}
    return startup


class ModelServer:
    def __init__(self, runtime, models, log):
        env = {**os.environ, 'AII_MODELS_DIR': str(models), 'AII_VOICE_LIMITS': worker_environment(default_limits())}
        worker = runtime / 'bin' / ('aii_voice_worker.exe' if os.name == 'nt' else 'aii_voice_worker')
        begun = time.monotonic()
        if os.name == 'nt':
            import msvcrt
            served_r, self.control_w = os.pipe()
            self.control_r, said_w = os.pipe()
            ends = [msvcrt.get_osfhandle(served_r), msvcrt.get_osfhandle(said_w)]
            env.update(AII_MODEL_CONTROL_IN=str(ends[0]), AII_MODEL_CONTROL_OUT=str(ends[1]))
            self.p = subprocess.Popen([str(worker), '--model-server'], cwd=runtime, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=log, env=env, startupinfo=inheritable(ends))
            os.close(served_r)
            os.close(said_w)
        else:
            self.control, theirs = socket.socketpair()
            env['AII_MODEL_CONTROL_FD'] = str(theirs.fileno())
            self.p = subprocess.Popen([str(worker), '--model-server'], cwd=runtime, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=log, env=env, pass_fds=(theirs.fileno(),))
            theirs.close()
        self.ready = json.loads(self.p.stdout.readline())['model_server_ready']
        self.ready_s = round(time.monotonic() - begun, 2)
        self.lock = threading.Lock()

    def channel(self):
        served_r, session_w = os.pipe()
        session_r, served_w = os.pipe()
        if os.name == 'nt':
            import _winapi
            import msvcrt
            mine = _winapi.GetCurrentProcess()
            given = [_winapi.DuplicateHandle(mine, msvcrt.get_osfhandle(fd), int(self.p._handle), 0, False,
                                             _winapi.DUPLICATE_SAME_ACCESS) for fd in (served_r, served_w)]
            with self.lock:
                os.write(self.control_w, struct.pack('<QQ', *given))
                word = os.read(self.control_r, 1)
            os.close(served_r)
            os.close(served_w)
            assert word == b'y', word
            return session_r, session_w
        with self.lock:
            socket.send_fds(self.control, [b'c'], [served_r, served_w])
            word = self.control.recv(1)
        os.close(served_r)
        os.close(served_w)
        assert word == b'y', word
        return session_r, session_w

    def close(self):
        if os.name == 'nt':
            os.close(self.control_w)
            os.close(self.control_r)
        else:
            self.control.close()
        return self.p.wait(timeout=30)


class Engine:
    def __init__(self, runtime, models, log, server=None):
        ir, self.iw = os.pipe()
        orr, ow = os.pipe()
        if os.name == 'nt':
            import msvcrt
            handles = [msvcrt.get_osfhandle(fd) for fd in (ir, ow)]
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {'handle_list': handles}
            inherit = {'startupinfo': startup}
        else:
            handles, inherit = [ir, ow], {'pass_fds': (ir, ow)}
        env = {**os.environ, 'AII_AUDIO_IN_FD': str(handles[0]), 'AII_AUDIO_OUT_FD': str(handles[1]),
               'AII_MODELS_DIR': str(models), 'AII_VOICE_LIMITS': worker_environment(default_limits())}
        mode, channel = [], None
        if server:
            channel = server.channel()
            mode = ['--session']
            if os.name == 'nt':
                import msvcrt
                ends = [msvcrt.get_osfhandle(fd) for fd in channel]
                env.update(AII_MODEL_CHANNEL_IN=str(ends[0]), AII_MODEL_CHANNEL_OUT=str(ends[1]))
                inherit = {'startupinfo': inheritable(handles + ends)}
            else:
                env.update(AII_MODEL_CHANNEL_IN=str(channel[0]), AII_MODEL_CHANNEL_OUT=str(channel[1]))
                inherit['pass_fds'] = inherit['pass_fds'] + tuple(channel)
        begun = time.monotonic()
        worker = runtime / 'bin' / ('aii_voice_worker.exe' if os.name == 'nt' else 'aii_voice_worker')
        self.p = subprocess.Popen([str(worker)] + mode, cwd=runtime, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=log, env=env, **inherit)
        os.close(ir)
        os.close(ow)
        for fd in channel or ():
            os.close(fd)
        self.audio = os.fdopen(self.iw, 'wb', buffering=0)
        self.output = os.fdopen(orr, 'rb', buffering=0)
        self.ready = json.loads(self.p.stdout.readline())['ready']
        self.ready_s = round(time.monotonic() - begun, 2)
        self.replies, self.events, self.errors = queue.Queue(), queue.Queue(), []
        self.streams = {}
        self.ended = {}
        self.storage = {}
        self.lock = threading.Lock()
        self.request = 0
        self.synthesis = 0
        threading.Thread(target=self.read_controls, daemon=True).start()
        threading.Thread(target=self.read_audio, daemon=True).start()

    def send(self, row):
        with self.lock:
            self.p.stdin.write((json.dumps(row, separators=(',', ':')) + '\n').encode())
            self.p.stdin.flush()

    def broker(self, q):
        key = q.get('resource', 'enrollment')
        action = q.get('action')
        reply = {'id': q['id'], 'session_id': q['session_id']}
        if not action:
            if key not in self.storage:
                reply.update(error='absent', reason_code='FS_NOT_FOUND')
            else:
                raw = self.storage[key]
                part = raw[q['offset']:q['offset'] + 65536]
                reply['value'] = {'offset': q['offset'], 'size': len(raw), 'bytes': len(part),
                                  'eof': q['offset'] + len(part) == len(raw),
                                  'data_b64': base64.b64encode(part).decode()}
                if q.get('digest'):
                    reply['value']['sha256'] = hashlib.sha256(raw).hexdigest()
        elif action == 'stage':
            staged = self.storage.setdefault('.stage:' + key, b'') if q.get('append') else b''
            staged += base64.b64decode(q['data_b64'])
            self.storage['.stage:' + key] = staged
            reply['value'] = {'bytes': len(staged), 'size': len(staged)}
        else:
            replaced = key in self.storage
            self.storage[key] = self.storage.pop('.stage:' + key)
            reply['value'] = {'size': len(self.storage[key]), 'sha256': hashlib.sha256(self.storage[key]).hexdigest(),
                              'replaced': replaced, 'durable': True, 'durability': 'synced'}
        self.send({'snapshot_reply': reply})

    def read_controls(self):
        try:
            for line in self.p.stdout:
                row = json.loads(line)
                if 'snapshot_request' in row:
                    self.broker(row['snapshot_request'])
                elif 'settings_request' in row:
                    q = row['settings_request']
                    self.send({'settings_reply': {'id': q['id'], 'session_id': q['session_id'], 'values': VALUES}})
                elif 'event' in row:
                    self.events.put((time.monotonic(), row['event']))
                elif 'id' in row:
                    self.replies.put(row)
        except Exception as e:
            self.errors.append(repr(e))

    def read_audio(self):
        def exact(n):
            b = b''
            while len(b) < n:
                chunk = self.output.read(n - len(b))
                if not chunk:
                    return None
                b += chunk
            return b
        try:
            while True:
                head = exact(28)
                if head is None:
                    return
                magic, kind, stream, seq, start, n = struct.unpack('>4sB3xIIQI', head)
                payload = exact(n) if n else b''
                now = time.monotonic()
                s = self.streams.setdefault(stream, {'first': None, 'pcm': bytearray()})
                if kind == PCM:
                    if s['first'] is None:
                        s['first'] = now
                    s['pcm'] += payload
                elif kind == END:
                    self.ended[stream] = now
        except Exception as e:
            self.errors.append(repr(e))

    def call(self, op, timeout=30, **args):
        self.request += 1
        self.send({'id': self.request, 'operation': op, 'arguments': args})
        row = self.replies.get(timeout=timeout)
        assert row['id'] == self.request and 'error' not in row, row
        return row['result']

    def wait(self, kind, sid, timeout=120):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if self.errors:
                raise RuntimeError(self.errors)
            try:
                at, e = self.events.get(timeout=.05)
            except queue.Empty:
                continue
            assert e['type'] != 'failure', e
            if e['type'] == kind and e['session_id'] == sid:
                return at, e
        raise TimeoutError(kind)

    def session(self, sid):
        self.call('speech.session.open', session_id=sid, input_handle=sid + '-mic', output_handle=sid + '-out',
                  audio={'format': 's16le', 'input': {'rate': 16000, 'channels': 1, 'stream': 1000 + self.request},
                         'output': {'rate': 24000, 'channels': 1}})
        self.wait('session_ready', sid)
        return 1000 + self.request - 1

    def speak(self, sid):
        self.synthesis += 1
        name = 'r-%d' % self.synthesis
        begun = time.monotonic()
        stream = self.call('speech.session.synthesize', session_id=sid, synthesis_id=name, text=TEXT)['output_stream']
        until = time.monotonic() + 120
        while stream not in self.ended and time.monotonic() < until:
            time.sleep(.01)
        s = self.streams[stream]
        samples = len(s['pcm']) // 2
        self.call('speech.session.playback_report', session_id=sid, synthesis_id=name, output_stream=stream,
                  rendered_samples=samples, terminal=True)
        audio_s = samples / 24000
        made_s = self.ended[stream] - begun
        return {'first_pcm_ms': round((s['first'] - begun) * 1000), 'made_s': round(made_s, 3),
                'audio_s': round(audio_s, 3), 'rtf': round(made_s / audio_s, 3),
                'sha256': hashlib.sha256(bytes(s['pcm'])).hexdigest()}, bytes(s['pcm'])

    def hear(self, sid, stream, pcm16k, seq_base):
        position, seq = 0, seq_base
        for at in range(0, len(pcm16k), 2048):
            part = pcm16k[at:at + 2048]
            self.audio.write(Frame(PCM, stream, seq, position, part).encode())
            position += len(part) // 2
            seq += 1
        begun = time.monotonic()
        self.call('speech.session.finish_input', session_id=sid, stream_id=sid + '-mic', end_sample=position)
        self.audio.write(Frame(END, stream, seq, position, b'').encode())
        at, final = self.wait('transcript_final', sid)
        return {'final_after_end_ms': round((at - begun) * 1000), 'text': final.get('text', ''),
                'text_sha256': hashlib.sha256(final.get('text', '').encode()).hexdigest()}

    def close(self):
        self.p.stdin.close()
        try:
            self.p.wait(timeout=30)
        finally:
            self.audio.close()
            self.output.close()
        return self.p.returncode


def one_process(runtime, models, out, index, repeats, save_pcm=False, server=None):
    log = (out / ('worker-%d.log' % index)).open('wb')
    e = Engine(runtime, models, log, server)
    result = {'ready_s': e.ready_s, 'ready': e.ready, 'at_ready': memory(e.p.pid), 'replies': [], 'finals': []}
    reply_pcm = None
    for r in range(repeats):
        sid = 'm1-%d-%d' % (index, r)
        stream = e.session(sid)
        row, pcm = e.speak(sid)
        result['replies'].append(row)
        if save_pcm:
            (out / ('reply-%d-%d.s16' % (index, r))).write_bytes(pcm)
        reply_pcm = reply_pcm or resample_24k_to_16k(pcm)
        result['finals'].append(e.hear(sid, stream, reply_pcm, 0))
        e.call('speech.session.close', session_id=sid, mode='drain')
        e.wait('session_end', sid)
        result.setdefault('after_session', []).append(memory(e.p.pid))
    result['exit'] = e.close()
    log.close()
    return result


def main():
    a = argparse.ArgumentParser()
    a.add_argument('runtime', type=Path)
    a.add_argument('models', type=Path)
    a.add_argument('out', type=Path)
    a.add_argument('--repeats', type=int, default=3)
    a.add_argument('--processes', type=int, default=2)
    a.add_argument('--save-pcm', action='store_true', help="keep each reply's PCM (s16, 24 kHz) to compare builds")
    a.add_argument('--model-server', action='store_true', help='one model server; a session process per identity (W2d)')
    a.add_argument('--together', action='store_true', help='run the identities at the same time')
    args = a.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    server = ModelServer(args.runtime, args.models, (args.out / 'model-server.log').open('wb')) if args.model_server else None
    server_memory = {'at_ready': memory(server.p.pid)} if server else None

    def run(i):
        return one_process(args.runtime, args.models, args.out, i, args.repeats, args.save_pcm, server)
    if args.together:
        runs, faults = [None] * args.processes, []

        def guarded(i):
            try:
                runs[i] = run(i)
            except Exception as e:
                faults.append(repr(e))
        threads = [threading.Thread(target=guarded, args=(i,)) for i in range(args.processes)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not faults, faults
    else:
        runs = [run(i) for i in range(args.processes)]
    if server:
        server_memory['after_runs'] = memory(server.p.pid)
        server_memory['exit'] = server.close()
    replies = {row['sha256'] for run in runs for row in run['replies']}
    texts = {row['text'] for run in runs for row in run['finals']}
    summary = {
        'platform': platform.platform(), 'runtime': str(args.runtime), 'text': TEXT, 'values': VALUES,
        'ready_s': [r['ready_s'] for r in runs], 'at_ready': [r['at_ready'] for r in runs],
        'after_sessions': [r.get('after_session') for r in runs],
        'reply_first_pcm_ms': [row['first_pcm_ms'] for r in runs for row in r['replies']],
        'reply_rtf': [row['rtf'] for r in runs for row in r['replies']],
        'final_after_end_ms': [row['final_after_end_ms'] for r in runs for row in r['finals']],
        'tts_repeats_exactly': len(replies) == 1, 'tts_distinct_outputs': len(replies),
        'asr_repeats_exactly': len(texts) == 1, 'asr_distinct_texts': sorted(texts),
        'exits': [r['exit'] for r in runs],
        'model_server': dict(server_memory, ready=server.ready, ready_s=server.ready_s) if server else None,
        'together': args.together,
        'reply_sha256': sorted(replies),
    }
    (args.out / 'runs.json').write_text(json.dumps(runs, indent=2) + '\n')
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
