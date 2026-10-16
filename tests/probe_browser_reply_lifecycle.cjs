const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', 'runtime', 'voice_core', 'web');

function page(source, extra = {}) {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {
      value: '', textContent: '', disabled: false, selectedOptions: [{text: 'Speaker'}],
      replaceChildren() {}, add() {},
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {getElementById: get},
    Option: class { constructor(text, value) { this.text = text; this.value = value; } },
    location: {host: '127.0.0.1'},
    console, Promise, Float32Array, Uint8Array,
    atob: text => Buffer.from(text, 'base64').toString('binary'),
    ...extra,
  });
  vm.runInContext(fs.readFileSync(path.join(root, source), 'utf8'), context);
  return {context, get};
}

async function finishKeepsReplyPlayback() {
  const sent = [];
  const {context} = page('app.js', {
    navigator: {mediaDevices: {enumerateDevices: async () => []}},
    VoicePlayback: {canSelect: () => false},
    fetch: async () => ({json: async () => ({status: 'ready'})}),
    WebSocket: {OPEN: 1},
  });
  context.fakeSocket = {readyState: 1, send: raw => sent.push(JSON.parse(raw))};
  context.fakeRoute = {resume: async () => {}};
  context.fakeContext = {
    currentTime: 0,
    createBuffer: (_channels, length, rate) => ({duration: length / rate, copyToChannel() {}}),
    createBufferSource: () => {
      const node = {connect() {}, start() {}, stop() {}};
      context.lastNode = node;
      return node;
    },
  };
  vm.runInContext('ws=fakeSocket;route=fakeRoute;ctx=fakeContext;bus={};running=false;done.add("s1")', context);
  const pcm = Buffer.from(new Float32Array(2400).buffer).toString('base64');
  context.frame = {synthesis_id: 's1', pcm_f32le: pcm, sample_rate: 24000, end_sample: 2400};
  await vm.runInContext('audio(frame)', context);
  context.lastNode.onended();
  assert.deepEqual(sent.map(row => row.type), ['playback_start', 'played', 'playback_stop']);
  assert.equal(sent[1].samples, 2400);
}

function refusedReplyStaysCorrectable() {
  const sent = [];
  let socket;
  class FakeSocket {
    constructor() { socket = this; }
    send(raw) { sent.push(JSON.parse(raw)); }
  }
  const {context, get} = page('native.js', {WebSocket: FakeSocket});
  get('input').value = 'mic'; get('output').value = 'speaker';
  get('reply-mode').value = 'manual'; get('reply').value = 'invalid';
  get('start').onclick();
  const receive = row => socket.onmessage({data: JSON.stringify(row)});
  receive({type: 'reply_requested', session_id: 'session', turn_id: 't1', request_id: 'r1', text: 'Hello'});
  get('send-reply').onclick();
  receive({type: 'reply_refused', request_id: 'r1', reason: 'invalid text'});
  assert.equal(vm.runInContext('pendingReply.request_id', context), 'r1');
  assert.equal(get('send-reply').disabled, false);
  get('reply').value = 'Corrected response.';
  get('send-reply').onclick();
  assert.deepEqual(sent.filter(row => row.type === 'reply').map(row => row.text),
    ['invalid', 'Corrected response.']);
  receive({type: 'reply_accepted', request_id: 'r1'});
  assert.equal(vm.runInContext('pendingReply', context), null);
}

(async () => {
  await finishKeepsReplyPlayback();
  refusedReplyStaysCorrectable();
  console.log('browser reply lifecycle: PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
