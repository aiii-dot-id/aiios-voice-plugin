// Recorded input, real shipped voice.js and real Web Audio. No fake clock,
// fabricated playback receipt, microphone permission, or physical output.
import { bindTransport, StreamLane, lanesForTest, playbackForTest, hush,
  encodeStreamFrame, connectionLost } from '/voice.js';

const evidence = { scope: 'Chrome Web Audio + shipped voice module + actual host app/SDK/MLX; recorded input and fixed reply text; no physical audio or LLM quality claim',
  events: [], controls: [], states: [], toasts: window.proofToasts || [], started: performance.now() };
let socket, receiver, lane;
const now = () => performance.now();
const check = (ok, why) => { if (!ok) throw new Error(why); };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, why, limit=12000) {
  const end = now()+limit;
  while (!fn()) { if(now()>end) throw new Error('timeout: '+why); await sleep(10); }
}
function json(message) {
  evidence.controls.push({at: now(), message});
  if(message.voice?.playback?.terminal && message.voice.playback.stream===4) {
    evidence.controls.at(-1).suppressed_by_fixture=true; return;
  }
  socket.send(JSON.stringify(message));
}
const eventCount = type => evidence.events.filter(e => e.type===type).length;
const terminalReports = outcome => evidence.controls.filter(e => e.message.voice?.playback?.terminal && e.message.voice.playback.outcome===outcome);
const words = s => (s.toLowerCase().match(/[a-z0-9]+/g)||[]).join(' ');
const expected = words('Please keep the opening words cobalt lantern seventeen. The recovery reply is now complete.');
async function speak() {
  const r=await fetch('/__speak', {method:'POST'}); check(r.ok, await r.text());
}
async function feed(pcm, finish) {
  const origin=now();
  for(let off=0; off<pcm.length; off+=1009) {
    const n=Math.min(1009,pcm.length-off);
    socket.send(encodeStreamFrame(48000,1,1,++lane.seq,lane.samples,pcm.slice(off,off+n)));
    lane.samples+=n;
    await sleep(Math.max(0, origin+(off+n)/48-now()));
  }
  if(finish) lane.finishInput();
}
async function main() {
  const source=await (await fetch('/__input')).arrayBuffer();
  const pcm=new Int16Array(source);
  evidence.fixture_samples=pcm.length;
  socket=new WebSocket(location.origin.replace('http','ws')+'/ws'); socket.binaryType='arraybuffer';
  receiver=bindTransport(b=>socket.send(b),json);
  socket.onmessage=ev=>{
    try {
      if(typeof ev.data!=='string') { receiver.receiveFrame(ev.data); return; }
      const m=JSON.parse(ev.data);
      if(m.type==='voice_event') {
        evidence.events.push({at:now(), ...m.voice_event}); receiver.voiceEvent(m.voice_event);
      } else if(m.type==='voice_session') {
        evidence.states.push({at:now(),...m.voice_session}); receiver.sessionState(m.voice_session);
      } else if(m.type==='error') evidence.events.push({at:now(),type:'host_error',reason:m.message});
    } catch(err) { evidence.handler_error=String(err.stack||err); }
  };
  await until(()=>socket.readyState===WebSocket.OPEN,'socket open');
  lane=new StreamLane({sendFrame:b=>socket.send(b),sendJSON:json,rate:48000,channels:1,mode:'conversation'});
  lanesForTest().push(lane); lane.open();
  await until(()=>lane.isOpen,'engine session open');
  await until(()=>eventCount('session_ready')===1,'engine ready after open admission');
  await speak();
  const p=playbackForTest();
  await until(()=>p.sources.length>0 && p.ctx?.state==='running','real AudioContext running');
  await until(()=>p.rendered(1)>=2400,'at least 50 ms actually rendered before interruption');
  evidence.context={constructor:p.ctx.constructor.name,rate:p.ctx.sampleRate,state:p.ctx.state};
  const manual=now(); hush();
  evidence.manual_local_stop_ms=now()-manual;
  check(p.sources.length===0,'hush left live browser audio sources');
  await until(()=>eventCount('synthesis_cancelled')>=1,'held-ack synthesis cancelled');
  evidence.manual_retirement_ms=evidence.events.find(e=>e.type==='synthesis_cancelled').at-manual;
  check(evidence.manual_retirement_ms<250,'manual interruption waited behind held synthesis acknowledgement');
  await until(()=>eventCount('reply_refused')>=1,'stale delayed synthesis acknowledgement refused');
  await feed(pcm,false);
  // A live microphone keeps supplying silence while the person pauses.
  // Stopping fixture delivery is not evidence of acoustic silence.
  await feed(new Int16Array(96000),false);
  await until(()=>eventCount('transcript_final')>=1,'first recorded utterance final');
  await until(()=>terminalReports('drained').length>=1,'full recovery rendered by browser',18000);
  await until(()=>eventCount('synthesis_end')>=1,'full recovery ended');
  check(lane.isOpen,'first reply closed resident session');
  await speak();
  await until(()=>p.sources.length>0,'second active reply');
  const spoken=eventCount('interruption_requested');
  const feeding=feed(pcm,true);
  await until(()=>eventCount('interruption_requested')>spoken,'real VAD barge-in');
  await feeding;
  await until(()=>eventCount('transcript_final')>=2,'second recorded utterance final');
  await until(()=>terminalReports('drained').length>=2,'final recovery fully rendered',18000);
  await until(()=>evidence.states.some(s=>s.state==='closed'),'Finish drains and closes',18000);
  const finals=evidence.events.filter(e=>e.type==='transcript_final');
  check(finals.length===2 && finals.every(e=>words(e.text)===expected),'opening or later words missing');
  check(terminalReports('stopped').length>=2,'both interrupted streams need stopped receipts');
  check(eventCount('drain_requested')===1,'Finish must request exactly one drain');
  check(eventCount('drain_refused')===0,'engine refused final drain');
  check(eventCount('failed')===0 && eventCount('host_error')===0,'host or engine failure');
  check(!evidence.handler_error,evidence.handler_error);
  check(p.unresolved.size===0,'browser has unresolved receipts');
  evidence.playback=p.stats();
  const readback=await fetch('/__readback'); check(readback.ok,'host readback refused');
  evidence.host_readback=await readback.json();
  const snap=evidence.host_readback.snapshot;
  const expectedInput=(2*pcm.length+96000)/3;
  check(snap.lifecycle==='closed' && snap.input_completion?.end_sample===expectedInput &&
    snap.input_completion?.processed_end_sample===expectedInput,'exact admitted/processed tail readback');
  for(const stream of ['2','4']) {
    const st=evidence.host_readback.outputs[stream];
    check(st && st.EngineReceived===115200 && st.Written===230400 && st.Refused===0 && st.Ended,
      'full engine-to-browser sample equality on stream '+stream);
  }
  // A fresh session on the same resident worker is then abandoned by the
  // page; the host must Abort and return both endpoints without a receipt.
  lanesForTest().splice(0);
  lane=new StreamLane({sendFrame:b=>socket.send(b),sendJSON:json,rate:48000,channels:1,mode:'conversation'});
  lanesForTest().push(lane); lane.open();
  await until(()=>lane.isOpen,'fresh resident session');
  await until(()=>eventCount('session_ready')===2,'fresh engine session ready');
  socket.onclose=()=>connectionLost();
  socket.close();
  await until(()=>socket.readyState===WebSocket.CLOSED,'page disconnect');
  evidence.passed=true;
}
main().catch(err=>{evidence.passed=false;evidence.error=String(err.stack||err);}).finally(async()=>{
  evidence.finished=now();
  await fetch('/__result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(evidence)});
  if(socket && socket.readyState===WebSocket.OPEN) socket.close();
});

