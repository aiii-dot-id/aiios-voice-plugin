"""A disk-backed stand-in for the host's private-file operations, for proofs and tests."""
from scripts._assertions import require_assertions
require_assertions()
import base64, hashlib, json, queue, re, struct, threading
from pathlib import Path


def digest(data): return hashlib.sha256(data).hexdigest()


class Broker:
    def __init__(self, host, root):
        self.host,self.root=host,root; root.mkdir(exist_ok=True)
        self.stop=threading.Event();self.errors=[];self.calls=[]
        self.durable=True;self.conflict=False
        self.hold=threading.Event();self.hold.set();self.entered=threading.Event()
        self.thread=threading.Thread(target=self.serve);self.thread.start()
    def serve(self):
        try:
            while not self.stop.is_set():
                try: q=self.host.host_requests.get(timeout=.05)
                except queue.Empty: continue
                p=q['params'];op=p['operation'];target=p['target'];a=p['arguments'];name=target['path']
                assert target['root']=='private'
                permanent=name in ('uid/enrollment.json','uid/captures.json')
                assert permanent or re.fullmatch(r'uid/\.(enrollment|captures)-[a-f0-9]{64}\.pending',name)
                path=self.root/Path(name).name
                self.entered.set()
                while not self.hold.wait(.01):
                    if self.stop.is_set(): return
                if op=='fs.read':
                    assert permanent and set(a)=={'offset','length','digest'}
                    if not path.exists(): value={'status':'failed','reasonCode':'FS_NOT_FOUND'}
                    else:
                        raw=path.read_bytes();offset=a['offset'];assert 0<=offset<=len(raw)
                        data=raw[offset:offset+a['length']]
                        v={**target,'offset':offset,'size':len(raw),'bytes':len(data),'eof':offset+len(data)==len(raw),'data_b64':base64.b64encode(data).decode()}
                        if a['digest']: v['sha256']=digest(raw)
                        value={'status':'succeeded','operation_result':v}
                elif op=='fs.write':
                    assert not permanent and set(a)=={'data_b64','append'}
                    raw=base64.b64decode(a['data_b64'],validate=True);assert 0<len(raw)<=65536
                    path.write_bytes((path.read_bytes() if a['append'] and path.exists() else b'')+raw)
                    value={'status':'succeeded','operation_result':{**target,'bytes':len(raw),'size':path.stat().st_size,'appended':a['append']}}
                elif op=='fs.publish':
                    assert permanent and set(a) in ({'from','sha256','expected_absent'},{'from','sha256','expected_sha256'})
                    prefix='captures' if name=='uid/captures.json' else 'enrollment'
                    assert re.fullmatch(r'uid/\.'+prefix+r'-[a-f0-9]{64}\.pending',a['from'])
                    source=self.root/Path(a['from']).name;raw=source.read_bytes();assert digest(raw)==a['sha256']
                    before=path.read_bytes() if path.exists() else None
                    mismatch=(before is not None if a.get('expected_absent') else before is None or digest(before)!=a['expected_sha256'])
                    if mismatch or self.conflict: value={'status':'failed','reasonCode':'FS_GENERATION_MISMATCH'}
                    else:
                        source.replace(path)
                        value={'status':'succeeded','operation_result':{**target,'size':len(raw),'sha256':digest(raw),'replaced':before is not None,'durable':self.durable,'durability':'synced' if self.durable else 'unknown'}}
                else: raise AssertionError(op)
                self.calls.append({'operation':op,'path':name,'status':value['status']})
                raw=json.dumps({'jsonrpc':'2.0','id':q['id'],'result':value}).encode()
                with self.host.write_lock:
                    self.host.process.stdin.write(struct.pack('>I',len(raw))+raw);self.host.process.stdin.flush()
        except BaseException as e: self.errors.append(repr(e))
    def close(self):
        self.stop.set();self.hold.set();self.thread.join(2)
        assert not self.thread.is_alive() and not self.errors,self.errors
