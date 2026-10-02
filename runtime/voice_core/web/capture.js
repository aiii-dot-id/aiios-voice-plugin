class VoiceCapture extends AudioWorkletProcessor {
  constructor(){super();this.mic=new Float32Array(512);this.ref=new Float32Array(512);this.n=0;this.enabled=true;
    this.port.onmessage=e=>{if(e.data==='stop'&&this.enabled){
      this.enabled=false;const padding=this.n?512-this.n:0;
      if(this.n){this.mic.fill(0,this.n);this.ref.fill(0,this.n);
        const packet=new Float32Array(1024);packet.set(this.mic);packet.set(this.ref,512);
        this.port.postMessage(packet.buffer,[packet.buffer]);this.n=0;}
      this.port.postMessage({type:'capture_end',padding_samples:padding});
    }};}
  process(inputs){
    if(!this.enabled)return true;
    const mic=inputs[0]?.[0],ref=inputs[1]?.[0];
    if(!mic)return true;
    for(let i=0;i<mic.length;i++){
      this.mic[this.n]=mic[i];this.ref[this.n]=ref?.[i]||0;this.n++;
      if(this.n===512){const packet=new Float32Array(1024);packet.set(this.mic);packet.set(this.ref,512);
        this.port.postMessage(packet.buffer,[packet.buffer]);this.n=0;}
    }
    return true;
  }
}
registerProcessor('voice-capture',VoiceCapture);
