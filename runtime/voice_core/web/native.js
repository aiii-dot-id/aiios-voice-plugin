"use strict";
const $ = id => document.getElementById(id);
let ws = null, rows = [], finals = [], inputClosed = false, terminal = false;
let pendingReply = null;
function replyText(request) { return $("reply").value.replaceAll("{transcript}", request.text).replaceAll("{turn}", request.turn_id.replace(/^t/, "")); }
function sendReply() {
  if (!ws || !pendingReply || terminal) return;
  ws.send(JSON.stringify({type:"reply", session_id:pendingReply.session_id,
    turn_id:pendingReply.turn_id, request_id:pendingReply.request_id, text:replyText(pendingReply)}));
  $("send-reply").disabled = true;
}
function log(value) { rows.push(value); rows = rows.slice(-100); $("events").textContent = rows.map(x => JSON.stringify(x)).join("\n"); }
function active(value) { $("start").disabled = value || !$("input").value || !$("output").value; $("devices").disabled = value; $("input").disabled = value; $("output").disabled = value; $("interrupt").disabled = $("end").disabled = true; if (!value) $("send-reply").disabled = true; }
$("devices").onclick = async () => {
  try {
    const result = await fetch("/native-devices"); if (!result.ok) throw Error(await result.text());
    const data = await result.json();
    for (const [id, field, preferred] of [["input","input_channels","Studio Display Microphone"],["output","output_channels","Studio Display Speakers"]]) {
      const prior = $(id).value;
      const devices = data.devices.filter(d => d[field] > 0);
      $(id).replaceChildren();
      for (const d of devices) { const o = document.createElement("option"); o.value = d.uid; o.textContent = d.name; $(id).append(o); }
      const selected = devices.find(d => d.uid === prior) || devices.find(d => d.name === preferred);
      if (selected) $(id).value = selected.uid;
    }
    $("start").disabled = !$("input").value || !$("output").value;
    $("status").textContent = "Devices listed. Confirm both selections. No microphone capture until Start.";
  } catch (e) { $("status").textContent = e.message; }
};
$("start").onclick = () => {
  active(true); finals = []; inputClosed = terminal = false; pendingReply = null;
  $("transcript").textContent = ""; $("status").textContent = "Starting native host; waiting for verified microphone and reference audio…";
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => ws.send(JSON.stringify({type:"start", input_kind:"native_microphone", input_uid:$("input").value, output_uid:$("output").value, reply_mode:"application", reply:"Application-driven response."}));
  ws.onmessage = event => {
    let row;
    try { row = JSON.parse(event.data); } catch (error) { log({type: "malformed_frame", reason: String(error)}); return; }
    if (!row || typeof row !== "object" || Array.isArray(row)) { log({type: "malformed_frame", reason: "not a JSON object"}); return; }
    log(row);
    if (row.type === "reply_requested") {
      pendingReply = row; $("send-reply").disabled = false;
      $("pending-reply").textContent = `Application reply for ${row.turn_id}: ${row.text}. Deadline: 30 seconds.`;
      if ($("reply-mode").value !== "manual") sendReply();
    }
    if (row.type === "reply_accepted" || row.type === "reply_refused") {
      if (pendingReply?.request_id === row.request_id) { pendingReply = null; $("send-reply").disabled = true; }
      $("pending-reply").textContent = row.type === "reply_accepted" ? `Reply accepted for ${row.turn_id}.` : `Reply refused: ${row.reason}`;
    }
    if (row.type === "ready") { $("status").textContent = "Native microphone and reference verified; model input is live. Speak naturally."; $("interrupt").disabled = $("end").disabled = false; }
    if (row.type === "input_closed") { inputClosed = true; $("end").disabled = true; $("status").textContent = "Input closed by the host; draining final transcript and reply…"; }
    if (row.type === "event" && row.event.type.startsWith("transcript_")) {
      const final = row.event.type === "transcript_final";
      if (final && row.event.text) finals.push(row.event.text);
      $("transcript").textContent = [...finals, ...(final ? [] : [row.event.text + " …"])].join("\n\n");
    }
    if (row.type === "event" && row.event.type === "interruption_requested") $("status").textContent = `Reply interrupted: ${row.event.reason}. ${inputClosed ? "Input is closed." : "Keep speaking."}`;
    if (row.type === "native_playback") {
      const state = inputClosed ? "Input is closed; draining reply." : "Microphone remains live until Finish.";
      if (row.native.type === "playback_start") $("status").textContent = `Native reply playing. ${state}`;
      if (row.native.type === "playback_stop") $("status").textContent = `Reply stopped (${row.native.reason}). ${state}`;
    }
    if (row.type === "error" || row.type === "complete") {
      terminal = true; pendingReply = null; $("send-reply").disabled = true; $("interrupt").disabled = $("end").disabled = true;
      $("status").textContent = row.type === "error" ? `FAILED: ${row.reason}` : `Session saved: ${row.evidence}. Physical-word audit is separate.`;
    }
  };
  ws.onerror = () => { $("status").textContent = "Connection failed."; };
  ws.onclose = () => { if (!terminal) $("status").textContent = "Connection closed without a completed session. Evidence is not a pass."; active(false); ws = null; };
};
$("interrupt").onclick = () => ws?.send(JSON.stringify({type:"interrupt"}));
$("send-reply").onclick = sendReply;
$("end").onclick = () => { ws?.send(JSON.stringify({type:"end"})); $("end").disabled = true; $("status").textContent = "Input finish requested; waiting for host acknowledgement…"; };
