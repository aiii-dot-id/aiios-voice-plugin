'use strict';
// Prefer AudioContext routing; WebKit/Firefox may expose only media-element routing.
globalThis.VoicePlayback = {
  canSelect() {
    return typeof globalThis.AudioContext?.prototype.setSinkId === 'function' ||
      typeof globalThis.HTMLMediaElement?.prototype.setSinkId === 'function';
  },
  async connect(context, sinkId, makeAudio = () => new Audio()) {
    if (!sinkId) return {node: context.destination, kind: 'system-default', sinkId: 'default', close() {}, suspend() {}, async resume() {}};
    if (typeof context.setSinkId === 'function') {
      await context.setSinkId(sinkId);
      return {node: context.destination, kind: 'audio-context', sinkId, close() {}, suspend() {}, async resume() {}};
    }
    const player = makeAudio();
    if (typeof player.setSinkId !== 'function') {
      throw new Error('This browser supports only system-default playback. Choose System default, or use a browser that supports speaker selection. No device was changed.');
    }
    const destination = context.createMediaStreamDestination();
    let suspended = false, closed = false, generation = 0;
    const suspend = () => {
      // Stopping WebAudio sources does not flush a media element's queued
      // render audio. Stop at the final browser playback surface as well.
      player.muted = true;
      player.pause();
      player.srcObject = null;
      suspended = true;
      generation++;
    };
    const resume = async () => {
      if (closed) throw new Error('Playback route is closed');
      if (!suspended) return;
      const attempt = generation;
      player.srcObject = destination.stream;
      player.muted = false;
      try {await player.play();} catch(error) {
        if (generation !== attempt && !closed) return; // intentional interruption
        throw error;
      }
      if (closed) {suspend(); throw new Error('Playback route closed while resuming');}
      if (generation === attempt) suspended = false;
    };
    const close = () => {
      closed = true;
      suspend();
      destination.stream.getTracks().forEach(track => track.stop());
    };
    try {
      player.srcObject = destination.stream;
      await player.setSinkId(sinkId);
      await player.play();
      return {node: destination, kind: 'media-element', sinkId, close, suspend, resume};
    } catch (error) {
      close();
      throw new Error(`Playback routing failed: ${error.message}. No default-device fallback was used.`);
    }
  },
};
