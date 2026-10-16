"use strict";
const $ = id => document.getElementById(id);
let ws = null, pending = null, terminal = false, inputClosed = false, rows = [];
const messages = new Map();
function status(text) { $("status").textContent = text; }
function log(row) { rows.push(row); rows = rows.slice(-80); $("events").textContent = rows.map(x => JSON.stringify(x)).join("\n"); }
function message(id, role, text, state) {
  let element = messages.get(id);
  if (!element) { element = document.createElement("div"); element.className = `message ${role}`; element.append(document.createElement("small"), document.createElement("span")); $("conversation").append(element); messages.set(id, element); }
  element.firstChild.textContent = `${role === "user" ? "You" : "AII Voice"} · ${state}`;
  if (text !== null) element.lastChild.textContent = text;
  while (messages.size > 60) { const key = messages.keys().next().value; messages.get(key).remove(); messages.delete(key); }
}
function stateFor(sid, state) { message(`a${sid.replace(/^s/, "")}`, "assistant", null, state); }
function active(on) {
  for (const key of ["devices", "input", "output", "mode"]) $(key).disabled = on;
  $("start").disabled = on || !$("input").value || !$("output").value;
  $("interrupt").disabled = $("finish").disabled = true; $("abort").disabled = !on;
  if (!on) $("send").disabled = true;
}
async function json(url, options) { const response = await fetch(url, options); const result = await response.json(); if (!response.ok) throw Error(result.error || `HTTP ${response.status}`); return result; }
$("devices").onclick = async () => { try {
  const data = await json("/native-devices");
  for (const [key, channels, preferred] of [["input", "input_channels", "Studio Display Microphone"], ["output", "output_channels", "Studio Display Speakers"]]) {
    const prior = $(key).value, items = data.devices.filter(d => d[channels] > 0); $(key).replaceChildren();
    for (const item of items) { const option = document.createElement("option"); option.value = item.uid; option.textContent = item.name; $(key).append(option); }
    const chosen = items.find(d => d.uid === prior) || items.find(d => d.name === preferred); if (chosen) $(key).value = chosen.uid;
  }
  active(false); status("Devices listed. Confirm your selections, then Start.");
} catch (error) { status(error.message); } };
$("mode").onchange = () => { $("manual").hidden = $("mode").value !== "manual"; };
$("start").onclick = () => {
  active(true); terminal = inputClosed = false; pending = null; messages.clear(); $("conversation").replaceChildren(); rows = []; $("partial").textContent = "";
  status("Opening native audio…"); ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => ws.send(JSON.stringify({type:"start", input_kind:"native_microphone", input_uid:$("input").value, output_uid:$("output").value, application_mode:$("mode").value}));
  ws.onmessage = event => {
    let row;
    try { row = JSON.parse(event.data); } catch (error) { log({type: "malformed_frame", reason: String(error)}); return; }
    if (!row || typeof row !== "object" || Array.isArray(row)) { log({type: "malformed_frame", reason: "not a JSON object"}); return; }
    const detail = row.event || {}; log(row);
    if (row.type === "ready") { status("Listening. Ask a question or just start talking."); $("finish").disabled = $("interrupt").disabled = false; }
    if (row.type === "reply_requested") { pending = row; $("send").disabled = $("mode").value !== "manual"; if ($("mode").value === "manual") status("Your turn is ready. Supply a reply within 30 seconds."); }
    if (row.type === "application_thinking") status("Thinking locally. You can interrupt or keep speaking.");
    if (row.type === "application_answer") message(`a${row.turn_id.replace(/^t/, "")}`, "assistant", row.text, row.provider_failed ? "provider failed · notice" : "generated text · awaiting playback");
    if (row.type === "application_failure") status(`Conversation model: ${row.reason}. Speech controls remain available.`);
    if (row.type === "reply_accepted" || row.type === "reply_refused") { if (pending?.request_id === row.request_id) { pending = null; $("send").disabled = true; } if (row.type === "reply_refused") status(`Reply not admitted: ${row.reason}`); }
    if (row.type === "event" && detail.type === "transcript_partial") $("partial").textContent = `${detail.text} …`;
    if (row.type === "event" && detail.type === "transcript_final") { message(detail.utterance_id, "user", detail.text, "recognized"); $("partial").textContent = ""; }
    if (row.type === "event" && detail.type === "speech_start") { pending = null; $("send").disabled = true; }
    if (row.type === "native_playback") { const d = row.native; if (d.type === "playback_start") { stateFor(d.synthesis_id, "speaking"); status(inputClosed ? "Input closed; draining reply…" : "Speaking. Microphone is still listening."); } if (d.type === "playback_stop") { stateFor(d.synthesis_id, d.reason === "drained" ? "played in full" : "interrupted · only partly played"); status(inputClosed ? "Finishing and saving…" : "Listening."); } }
    if (row.type === "input_closed") { inputClosed = true; $("finish").disabled = true; status("Input closed. Preserving the final transcript and reply…"); }
    if (row.type === "error" || row.type === "complete") { terminal = true; $("finish").disabled = $("interrupt").disabled = $("send").disabled = true; status(row.type === "error" ? `Session failed: ${row.reason}` : `Finished and saved locally: ${row.evidence}`); }
  };
  ws.onerror = () => status("Connection error. No successful completion claimed.");
  ws.onclose = () => { if (!terminal) status("Session aborted/disconnected. Audio stopped; this was not a complete drain."); ws = null; pending = null; active(false); };
};
$("interrupt").onclick = () => ws?.send(JSON.stringify({type:"interrupt"}));
$("finish").onclick = () => { ws?.send(JSON.stringify({type:"end"})); $("finish").disabled = true; status("Finish requested; waiting for native input closure…"); };
$("abort").onclick = () => { ws?.send(JSON.stringify({type:"abort"})); $("abort").disabled = true; };
$("send").onclick = () => { if (!pending || !ws) return; message(`a${pending.turn_id.replace(/^t/, "")}`, "assistant", $("reply").value, "manual · awaiting admission"); ws.send(JSON.stringify({...pending, type:"reply", text:$("reply").value})); $("send").disabled = true; };
for (const operation of ["enroll", "identify", "list", "remove", "reset"]) $(operation).onclick = async () => {
  try {
    if (operation === "reset" && !confirm("Remove every stored speaker enrollment? Conversation history and audio evidence are not removed.")) return;
    if (operation === "remove" && !confirm(`Remove all enrollment vectors for ${$("speaker").value}?`)) return;
    let url = `/uid/${operation}`, options;
    if (operation === "enroll" || operation === "identify") { const file = $("wav").files[0]; if (!file) throw Error("Select a 16 kHz mono PCM16 WAV first."); url += `?speaker_id=${encodeURIComponent($("speaker").value)}&label=${encodeURIComponent($("label").value)}`; options = {method:"POST", body:file, headers:{"Content-Type":"application/octet-stream"}}; }
    else if (operation !== "list") options = {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({speaker_id:$("speaker").value, confirm:operation === "reset"})};
    $("uid-result").textContent = "Speaker operation running independently of conversation…";
    $("uid-result").textContent = JSON.stringify(await json(url, options), null, 2);
  } catch (error) { $("uid-result").textContent = error.message; }
};
json("/health").then(h => { $("backend").textContent = `${h.status} · ${h.model}`; }).catch(e => { $("backend").textContent = e.message; });
