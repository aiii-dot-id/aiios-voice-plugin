'use strict';
const $=id=>document.getElementById(id);
let ws,ctx,stream,worklet,bus,route,clock=0,running=false,playId=null,played=0,done=new Set(),cancelled=new Set(),sources=new Set(),audioChain=Promise.resolve();
function log(value){$('events').textContent=(JSON.stringify(value)+'\n'+$('events').textContent).slice(0,18000);}
function send(value){if(ws?.readyState===WebSocket.OPEN)ws.send(JSON.stringify(value));}
async function devices(){
  const rows=await navigator.mediaDevices.enumerateDevices();
  for(const [id,kind] of [['mic','audioinput'],['speaker','audiooutput']]){
    const old=$(id).value;$(id).replaceChildren(new Option('System default',''));
    for(const row of rows.filter(r=>r.kind===kind&&(id!=='speaker'||VoicePlayback.canSelect())))$(id).add(new Option(row.label||row.deviceId,row.deviceId));
    $(id).value=old;
  }
  $('routing').textContent=VoicePlayback.canSelect()?'Explicit speaker selection is supported.':'This browser supports system-default output only; device-specific choices are unavailable. Check your system output before starting.';
}
function stopPlayback(notify=true,expectedSid=playId){
  if(expectedSid!==playId)return;
  const sid=playId;if(sid)cancelled.add(sid);
  for(const node of sources){node.onended=null;try{node.stop();}catch{}}sources.clear();
  route?.suspend();
  if(sid)send({type:'playback_stop',synthesis_id:sid});
  playId=null;clock=ctx?.currentTime||0;
  if(notify)send({type:'interrupt'});
}
function maybeStop(sid){if(done.has(sid)&&!sources.size&&playId===sid){send({type:'playback_stop',synthesis_id:sid});playId=null;}}
async function audio(message){
  const sid=message.synthesis_id;if(cancelled.has(sid))return;
  if(playId&&playId!==sid)stopPlayback(false);
  await route.resume();
  if(!running||cancelled.has(sid))return;
  const bytes=Uint8Array.from(atob(message.pcm_f32le),c=>c.charCodeAt(0));
  const samples=new Float32Array(bytes.buffer),buffer=ctx.createBuffer(1,samples.length,message.sample_rate);
  buffer.copyToChannel(samples,0);const node=ctx.createBufferSource();node.buffer=buffer;node.connect(bus);
  if(!playId){playId=sid;played=0;send({type:'playback_start',synthesis_id:sid});}
  const at=Math.max(ctx.currentTime+.02,clock);clock=at+buffer.duration;sources.add(node);
  node.onended=()=>{sources.delete(node);if(playId===sid){played=message.end_sample;send({type:'played',synthesis_id:sid,samples:played});maybeStop(sid);}};
  node.start(at);
}
async function cleanup(){running=false;if(worklet)worklet.port.postMessage('stop');stream?.getTracks().forEach(t=>t.stop());
  stopPlayback(false);route?.close();route=null;if(ctx)await ctx.close();ctx=null;$('start').disabled=false;$('stop').disabled=true;$('interrupt').disabled=true;}
async function start(){
  $('start').disabled=true;$('status').textContent='Requesting microphone…';
  try{
    const mic=$('mic').value,sink=$('speaker').value;
    stream=await navigator.mediaDevices.getUserMedia({audio:{deviceId:mic?{exact:mic}:undefined,
      channelCount:1,echoCancellation:{exact:true},noiseSuppression:true,autoGainControl:false}});
    ctx=new AudioContext({sampleRate:16000});await ctx.resume();
    if(ctx.sampleRate!==16000)throw Error('AudioContext did not accept 16kHz; refusing unlabelled resampling');
    $('status').textContent='Connecting local WebRTC playback…';
    route=await VoiceRTCPlayback.connect(ctx,sink);
    await ctx.audioWorklet.addModule('/capture.js');
    worklet=new AudioWorkletNode(ctx,'voice-capture',{numberOfInputs:2,numberOfOutputs:1,outputChannelCount:[1]});
    bus=ctx.createGain();bus.gain.value=.6;bus.connect(route.node);route.referenceNode.connect(worklet,0,1);
    const mute=ctx.createGain();mute.gain.value=0;worklet.connect(mute).connect(ctx.destination);
    const input=ctx.createMediaStreamSource(stream);
    ws=new WebSocket(`ws://${location.host}/ws`);cancelled=new Set();done=new Set();sources=new Set();audioChain=Promise.resolve();clock=ctx.currentTime;
    ws.onopen=()=>send({type:'start',input_kind:'browser_microphone',reply:$('reply').value,
      audio_settings:{...stream.getAudioTracks()[0].getSettings(),microphoneLabel:stream.getAudioTracks()[0].label,contextSampleRate:ctx.sampleRate,sinkId:route.sinkId,playbackRoute:route.kind,playbackLabel:$('speaker').selectedOptions[0]?.text,automaticInterruption:'browser-webrtc-aec-vad',rawRmsBargeIn:false}});
    ws.onmessage=e=>{let m;try{m=JSON.parse(e.data);}catch(error){log({type:'malformed_frame',reason:String(error)});return;}
      if(!m||typeof m!=='object'||Array.isArray(m)){log({type:'malformed_frame',reason:'not a JSON object'});return;}
      if(m.type==='ready'){running=true;input.connect(worklet,0,0);$('stop').disabled=false;$('interrupt').disabled=false;$('status').textContent='Listening · local recording active';log(m);}
      else if(m.type==='audio'){audioChain=audioChain.then(()=>audio(m)).catch(error=>{$('status').textContent='Playback failed: '+error.message;ws.close();});}
      else if(m.type==='interrupt'){cancelled.add(m.synthesis_id);stopPlayback(false,m.synthesis_id);log(m);}
      else if(m.type==='synthesis_done'){done.add(m.synthesis_id);maybeStop(m.synthesis_id);log(m);}
      else if(m.type==='event'){const v=m.event;if(v.type.startsWith('transcript_'))$('transcript').textContent=v.text;log(v);}
      else {log(m);if(m.type==='complete')$('status').textContent='Saved: '+m.evidence;if(m.type==='error')$('status').textContent='Failed: '+m.reason;}
    };
    ws.onclose=()=>{cleanup();};ws.onerror=()=>{$('status').textContent='Local voice connection failed';};
    worklet.port.onmessage=e=>{if(!running)return;
      if(e.data?.type==='capture_end'){running=false;stream?.getTracks().forEach(t=>t.stop());
        send({type:'end',input_padding_samples:e.data.padding_samples});return;}
      if(ws.bufferedAmount>262144){$('status').textContent='Network audio backlog: stopping without silent drops';ws.close();return;}
      ws.send(e.data);
    };
  }catch(error){$('status').textContent=error.message;log({error:String(error)});await cleanup();}
}
$('devices').onclick=async()=>{try{
  if(!running){const probe=await navigator.mediaDevices.getUserMedia({audio:true});probe.getTracks().forEach(t=>t.stop());}
  await devices();
}catch(e){log({error:String(e)});}};
$('check-playback').onclick=async()=>{
  if(running)return;
  $('check-playback').disabled=true;let probeContext,probeRoute;
  try{
    probeContext=new AudioContext({sampleRate:16000});await probeContext.resume();
    probeRoute=await VoiceRTCPlayback.connect(probeContext,$('speaker').value);
    $('status').textContent='Local WebRTC playback connected. No microphone used; acoustic qualification still requires speaking.';
    log({playback_connection:'passed',route:probeRoute.kind,microphone_used:false});
  }catch(error){$('status').textContent='Playback connection failed: '+error.message;log({error:String(error)});}
  finally{probeRoute?.close();if(probeContext)await probeContext.close();$('check-playback').disabled=false;}
};
$('start').onclick=start;$('interrupt').onclick=()=>stopPlayback(true);
$('stop').onclick=()=>{$('stop').disabled=true;worklet?.port.postMessage('stop');$('status').textContent='Finishing and saving evidence…';};
fetch('/health').then(r=>r.json()).then(v=>{$('status').textContent=v.status==='ready'?'Models ready. Select devices and start.':'Not ready';log(v);}).catch(e=>{$('status').textContent=String(e);});
devices().catch(e=>log({error:String(e)}));
