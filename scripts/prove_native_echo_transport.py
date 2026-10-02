"""Real worker/AEC with explicit fake recognition models; no physical audio."""
import argparse, json, struct, time
from pathlib import Path
from scripts.prove_native_worker_transport import Worker

def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();args.out.mkdir(exist_ok=False)
    cases=[]
    w=Worker(args.binary,args.out/'worker')
    try:
        for i, length in enumerate((1,159,160,161,321,12001)):
            sid='echo-'+str(i)
            roles=['capture','playback_reference']
            r,_=w.call('open',session_id=sid,input_handle='capture',output_handle='playback',audio={'format':'s16le','input':{'rate':48000,'channels':2,'channel_roles':roles,'processing':{'echo_cancellation':False,'tested':False}},'output':{'rate':48000,'channels':1}})
            assert r['audio']['input']==dict(rate=16000,channels=2,channel_roles=roles),r
            w.configure(w.settings.get(timeout=2),768);w.event('session_ready',sid)
            # Alternate Finish before the last frame, and after all PCM.
            if i%2==0:w.call('finish_input',session_id=sid,stream_id='capture',end_sample=length)
            seq=0
            for start in range(0,length,127):
                n=min(127,length-start);seq+=1
                pcm=struct.pack('<hh',2048,0)*n
                w.input.write(struct.pack('>4sB3xIIQI',b'AUD1',1,9,seq,start,len(pcm))+pcm)
            if i%2:w.call('finish_input',session_id=sid,stream_id='capture',end_sample=length)
            w.input.write(struct.pack('>4sB3xIIQI',b'AUD1',3,9,seq+1,length,0))
            e=w.event('input_finished',sid,timeout=8)
            assert e['end_sample']==length,e
            s=w.status(sid);assert s['input']['received_end_sample']==length,s
            assert s['input']['processing']['engine_echo_cancellation'] is True,s
            w.call('close',session_id=sid,mode='drain');w.event('session_end',sid,timeout=8)
            cases.append(dict(samples=length,finish_before_pcm=i%2==0,passed=True))
        code=w.close();w=None;assert code==0,code
        (args.out/'result.json').write_text(json.dumps(dict(passed=True,cases=cases,scope=__doc__))+'\n')
    finally:
        if w:w.close()
if __name__=='__main__':main()
