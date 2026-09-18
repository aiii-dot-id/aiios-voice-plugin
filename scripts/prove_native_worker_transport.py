"""Unmodified native worker transport, fake models; no devices or model inference."""
import argparse,json,os,queue,struct,subprocess,threading,time,hashlib
from pathlib import Path

class Worker:
    def __init__(self,binary,out,drain=True):
        self.events=[];self.frames=[];self.replies=queue.Queue();self.settings=queue.Queue();self.counter=0
        self.all=[];self.errors=[];self.control_done=threading.Event();self.out=out;out.mkdir(parents=True,exist_ok=False)
        r,w=os.pipe();rr,ww=os.pipe();self.input=os.fdopen(w,'wb',buffering=0);self.output=os.fdopen(rr,'rb',buffering=0)
        env={**os.environ};creation={}
        if os.name=='nt':
            import msvcrt
            handles=[msvcrt.get_osfhandle(fd) for fd in (r,ww)]
            for handle in handles:os.set_handle_inheritable(handle,True)
            startup=subprocess.STARTUPINFO();startup.lpAttributeList={'handle_list':handles};creation={'startupinfo':startup}
        else:handles=[r,ww];creation={'pass_fds':tuple(handles)}
        env.update(dict(zip(('AII_AUDIO_IN_FD','AII_AUDIO_OUT_FD'),map(str,handles))))
        self.log=(out/'stderr.log').open('wb')
        self.p=subprocess.Popen([str(binary),*['fixture']*7],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,bufsize=0,**creation)
        (out/'owner.json').write_text(json.dumps({'pid':self.p.pid,'binary':str(binary),'sha256':hashlib.sha256(Path(binary).read_bytes()).hexdigest()})+'\n')
        os.close(r);os.close(ww)
        def controls():
            try:
                for raw in self.p.stdout:
                    row=json.loads(raw);self.all.append(row)
                    if 'event' in row:self.events.append(row['event'])
                    elif 'settings_request' in row:self.settings.put(row['settings_request'])
                    else:self.replies.put(row)
            except Exception as e:self.errors.append(repr(e))
            finally:self.control_done.set()
        self.thread=threading.Thread(target=controls,daemon=True);self.thread.start()
        def audio():
            try:
                while True:
                    def exact(n):
                        b=b''
                        while len(b)<n:
                            chunk=self.output.read(n-len(b))
                            if not chunk:
                                if b:raise EOFError('truncated frame')
                                return None
                            b+=chunk
                        return b
                    head=exact(28)
                    if head is None:return
                    magic,kind,stream,seq,start,n=struct.unpack('>4sB3xIIQI',head)
                    assert magic==b'AUD1' and n<=65536
                    payload=exact(n) if n else b'';assert payload is not None
                    self.frames.append({'kind':kind,'stream':stream,'seq':seq,'start':start,'samples':n//2})
            except Exception as e:self.errors.append(repr(e))
        self.audio_thread=threading.Thread(target=audio,daemon=True) if drain else None
        if self.audio_thread:self.audio_thread.start()
        self.ready=self.replies.get(timeout=8);assert self.ready['ready']['identity']['backend']=='fixture-native'
    def send(self,row):self.p.stdin.write(json.dumps(row).encode()+b'\n');self.p.stdin.flush()
    def call(self,op,**args):
        self.counter+=1;begun=time.monotonic();self.send({'id':self.counter,'operation':'speech.session.'+op,'arguments':args})
        row=self.replies.get(timeout=2);assert row['id']==self.counter,row
        assert 'error' not in row,row
        return row['result'],time.monotonic()-begun
    def event(self,kind,sid,timeout=5):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            for e in self.events:
                if e['type']==kind and e['session_id']==sid:return e
            # Process exit can precede the reader consuming its final buffered
            # stdout bytes. Only reader EOF proves an event cannot still arrive.
            if self.control_done.is_set():
                for e in self.events:
                    if e['type']==kind and e['session_id']==sid:return e
                raise RuntimeError('control EOF before '+kind)
            time.sleep(.002)
        raise TimeoutError(kind)
    def open(self,sid,settings=True):
        r,_=self.call('open',session_id=sid,input_handle='capture',output_handle='playback',audio={'format':'s16le','input':{'rate':48000,'channels':1},'output':{'rate':48000,'channels':2}})
        assert r['audio']=={'input':{'rate':16000,'channels':1},'output':{'rate':24000,'channels':1}}
        q=self.settings.get(timeout=2)
        if settings:self.configure(q,768);self.event('session_ready',sid)
        return q
    def configure(self,q,pause):self.send({'settings_reply':{**q,'values':{'turn_pause_ms':pause}}})
    def status(self,sid):return self.call('status',session_id=sid)[0]
    def close(self):
        if not self.p.stdin.closed:self.p.stdin.close()
        if not self.input.closed:self.input.close()
        try:code=self.p.wait(timeout=8)
        except subprocess.TimeoutExpired:self.p.kill();self.p.wait(timeout=3);raise
        finally:
            self.thread.join(timeout=1)
            if self.audio_thread:self.audio_thread.join(timeout=1)
            self.output.close();self.log.close()
            (self.out/'events.json').write_text(json.dumps(self.all,indent=2)+'\n')
            (self.out/'frames.json').write_text(json.dumps(self.frames,indent=2)+'\n')
            (self.out/'retirement.json').write_text(json.dumps({'pid':self.p.pid,'exit_code':self.p.poll()})+'\n')
        assert not self.errors,self.errors
        return code

def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--out',type=Path,required=True);args=p.parse_args();args.out.mkdir(exist_ok=False,parents=True)
    report={'passed':False,'scope':__doc__,'binary_sha256':hashlib.sha256(args.binary.read_bytes()).hexdigest(),'cases':[]};w=None
    def save():(args.out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    try:
        env={k:v for k,v in os.environ.items() if k!='AII_MODELS_DIR'}
        missing=subprocess.run([str(args.binary)],env=env,capture_output=True,timeout=8)
        assert missing.returncode!=0 and b'host-provided model root required' in missing.stderr
        report['missing_model_root_refused']=True
        # Model owner blocked in next(): both control admissions remain prompt,
        # and Stop does not secretly cancel computation.
        w=Worker(args.binary,args.out/'held-inference');w.open('hold')
        w.call('synthesize',session_id='hold',synthesis_id='held',text='Hold.');w.event('synthesis_start','hold')
        _,stop=w.call('stop_playback',session_id='hold',synthesis_id='held');assert w.status('hold')['synthesis']['state']=='running'
        _,cancel=w.call('cancel_synthesis',session_id='hold',synthesis_id='held');w.event('synthesis_cancelled','hold')
        assert stop<1 and cancel<1
        w.call('close',session_id='hold',mode='abort');assert w.event('session_end','hold')['status']=='aborted';assert w.close()==0;w=None
        report['cases'].append({'name':'independent-controls-with-blocked-inference','stop_s':stop,'cancel_s':cancel})
        # A delayed old settings answer cannot configure a new session, and
        # aborting an opening session does not wait on settings or model work.
        w=Worker(args.binary,args.out/'settings');old=w.open('old',False);_,elapsed=w.call('close',session_id='old',mode='abort');assert elapsed<1;w.event('session_end','old')
        new=w.open('new',False);w.configure(old,320);assert w.status('new')['lifecycle']=='opening';w.configure(new,1000)
        ready=w.event('session_ready','new')
        expected={'turn_pause_ms':1000,'vad_threshold':.5,'tts_voice':'alba','tts_language':'en',
                  'stt_language':'en','tts_temperature':struct.unpack('f',struct.pack('f',.3))[0],'tts_seed':20260908,'capture_limit_minutes':30}
        observed=w.status('new')['operator_settings']
        # cJSON prints the float32 value as decimal; do not demand Python's
        # binary64 parse recreate its final ULP. Every other key is exact.
        assert abs(observed['tts_temperature']-expected['tts_temperature'])<1e-12
        expected['tts_temperature']=observed['tts_temperature']
        assert observed==expected
        assert ready['models']['operator_settings']==expected
        w.call('close',session_id='new',mode='abort');w.event('session_end','new');assert w.close()==0;w=None;report['cases'].append({'name':'late-settings-and-abort-custody'})
        # Responses already admitted before private EOF cannot disappear while
        # the process reports success. Repeat enough to exercise both schedules.
        for index in range(20):
            w=Worker(args.binary,args.out/f'eof-{index}');w.open('eof')
            w.counter+=1;w.send({'id':w.counter,'operation':'speech.session.status','arguments':{'session_id':'eof'}});w.p.stdin.close()
            row=w.replies.get(timeout=3);assert row['id']==w.counter and 'result' in row
            w.event('session_end','eof');assert w.close()==0;w=None
        report['cases'].append({'name':'queued-response-before-eof','runs':20})
        # The complete 13.855-second recording is already available. Sending
        # many ordinary frames after Finish must not cost a scheduler tick
        # per frame and manufacture a missing-tail fault. No timer-resolution
        # changes, enlarged deadline or extra padding are allowed.
        w=Worker(args.binary,args.out/'queued-tail');w.open('queued-tail')
        samples=221680;w.call('finish_input',session_id='queued-tail',stream_id='capture',end_sample=samples)
        begun=time.monotonic()
        for seq,start in enumerate(range(0,samples,1024),1):
            payload=b'\x00\x10'*min(1024,samples-start)
            w.input.write(struct.pack('>4sB3xIIQI',b'AUD1',1,1,seq,start,len(payload))+payload)
        w.input.write(struct.pack('>4sB3xIIQI',b'AUD1',3,1,seq+1,samples,0))
        sent=time.monotonic()-begun
        w.call('close',session_id='queued-tail',mode='drain')
        complete=w.event('session_end','queued-tail')
        assert complete['status']=='completed' and complete['input_samples']==samples,complete
        assert not any(e['type']=='failure' for e in w.events),w.events
        assert w.close()==0;w=None
        report['cases'].append({'name':'queued-final-tail-without-scheduler-tax','samples':samples,'send_seconds':sent})
        w=Worker(args.binary,args.out/'missing-tail');w.open('tail');w.call('finish_input',session_id='tail',stream_id='capture',end_sample=1024);w.call('close',session_id='tail',mode='drain')
        failure=w.event('failure','tail');assert failure['resources_released'] and 'tail' in failure['reason'].lower(),failure
        assert not any(e['type']=='input_finished' for e in w.events);assert w.close()!=0;w=None;report['cases'].append({'name':'missing-input-tail-fails-with-custody'})
        # Stop/Cancel must pass while the audio sink refuses to read at all.
        # A partially written frame cannot be salvaged as a successful lane.
        w=Worker(args.binary,args.out/'backpressure',False);w.open('pressure');w.call('synthesize',session_id='pressure',synthesis_id='flood',text='Flood.');w.event('synthesis_start','pressure');time.sleep(.1)
        _,stop=w.call('stop_playback',session_id='pressure',synthesis_id='flood');_,cancel=w.call('cancel_synthesis',session_id='pressure',synthesis_id='flood');assert stop<1 and cancel<1
        code=w.close();assert code!=0;w=None;report['cases'].append({'name':'full-output-pipe','stop_s':stop,'cancel_s':cancel,'exit':code})
        report['passed']=True;print(json.dumps(report))
    except Exception as e:report['error']=repr(e);raise
    finally:
        if w:
            try:report['cleanup_exit']=w.close()
            except Exception as e:report['cleanup_error']=repr(e)
        save()
if __name__=='__main__':main()
