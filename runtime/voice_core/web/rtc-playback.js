'use strict';
// Entirely local peer pair: no signalling service, STUN, TURN, or remote peer.
// Render through WebRTC so capture AEC can observe the decoded playback path.
globalThis.VoiceRTCPlayback = {
  async connect(context, sinkId, api = globalThis) {
    if (typeof api.RTCPeerConnection !== 'function') throw Error('This browser does not provide WebRTC playback. Use a current browser with WebRTC support.');
    let sender, receiver, destination, player;
    let remote, closed = false, suspended = false, generation = 0;
    const pending = new Set();
    function wait(target, event, test, label) {
      if (test()) return Promise.resolve();
      return new Promise((resolve, reject) => {
        const finish = error => {clearTimeout(timer);target.removeEventListener(event, changed);pending.delete(cancel);error ? reject(error) : resolve();};
        const changed = () => {if(test())finish();};
        const cancel = () => finish(Error('Playback route closed'));
        const timer = setTimeout(() => finish(Error(label+' timed out')), 5000);
        pending.add(cancel);target.addEventListener(event, changed);
      });
    }
    function suspend() {
      generation++;suspended = true;if(player){player.muted = true;player.pause();player.srcObject = null;}
    }
    function close() {
      if(closed)return;closed = true;suspend();
      for(const cancel of [...pending])cancel();
      sender?.close();receiver?.close();
      destination?.stream.getTracks().forEach(track => track.stop());
      remote?.getTracks().forEach(track => track.stop());
    }
    async function resume() {
      if(closed)throw Error('Playback route is closed');
      if(!suspended)return;
      const attempt = generation;player.srcObject = remote;player.muted = false;
      try {await player.play();} catch(error) {if(generation!==attempt&&!closed)return;throw error;}
      if(closed)throw Error('Playback route is closed');
      if(generation===attempt)suspended = false;
    }
    try {
      sender = new api.RTCPeerConnection({iceServers: []});
      receiver = new api.RTCPeerConnection({iceServers: []});
      destination = context.createMediaStreamDestination();
      player = new api.Audio();
      if(sinkId) {
        if(typeof player.setSinkId!=='function')throw Error('WebRTC playback requires media-element speaker selection; choose System default if this browser cannot select a speaker.');
        await player.setSinkId(sinkId);
      }
      receiver.addEventListener('track', event => {remote = event.streams[0] || new api.MediaStream([event.track]);});
      for(const track of destination.stream.getTracks())sender.addTrack(track, destination.stream);
      await sender.setLocalDescription(await sender.createOffer());
      await wait(sender, 'icegatheringstatechange', () => sender.iceGatheringState==='complete', 'Local playback ICE gathering');
      await receiver.setRemoteDescription(sender.localDescription);
      await receiver.setLocalDescription(await receiver.createAnswer());
      await wait(receiver, 'icegatheringstatechange', () => receiver.iceGatheringState==='complete', 'Local playback ICE gathering');
      await sender.setRemoteDescription(receiver.localDescription);
      await wait(receiver, 'connectionstatechange', () => receiver.connectionState==='connected', 'Local playback connection');
      if(!remote)throw Error('Local playback connection has no audio track');
      player.srcObject = remote;await player.play();
      return {node: destination, referenceNode: context.createMediaStreamSource(remote),
              kind: 'local-webrtc-media-element', sinkId: sinkId || 'default',
              suspend, resume, close};
    } catch(error) {close();throw error;}
  },
};
