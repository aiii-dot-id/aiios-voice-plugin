# Standalone application-controlled voice session

The candidate on `http://127.0.0.1:9138` uses the existing pinned recognizer,
synthesizer and native audio host. The preserved reference remains on port 9137.
This is a local development interface, not the Plugin SDK's JSON-RPC wire and
not an assertion that a reasoning LLM is connected. The native example's automatic
mode expands an explicitly labelled application reply template on each turn.

## Run and use

From the voice-frontier root, using its installed MLX environment:

```sh
"/home/user/work/mlx-quant-sota/.venv-311/bin/python" \
  scripts/serve_voice_reference.py --port 9138 \
  --output deliverables/native-conversation/sessions
```

Only start a server if that port is unoccupied. Open its native page, list
devices, confirm the microphone and speakers, and explicitly Start. Device
listing never opens capture. Replies may include `{transcript}` and `{turn}`;
the text remains editable during the session. Manual mode waits for **Send
application reply** instead of responding automatically. Finish closes input
and drains the final eligible reply. Closing the page aborts owned session work.

The server binds loopback, requires its exact Origin for WebSocket admission,
allows one session and refuses browser-supplied audio or playback acknowledgements
in native mode. No model/provider credential is needed or sent.

## Application control

Open `/ws` using Origin `http://127.0.0.1:9138`. Start native capture:

```json
{"type":"start","input_kind":"native_microphone","reply_mode":"application","reply":"Unused fixed-mode text.","input_uid":"SELECTED INPUT UID","output_uid":"SELECTED OUTPUT UID"}
```

The server first sends `starting`. Only `ready` confirms validated native
microphone/reference input is reaching the model. Neither means a user has spoken.

For every eligible committed turn, the application receives:

```json
{"type":"reply_requested","session_id":"SESSION","turn_id":"t1","request_id":"OPAQUE ID","text":"Recognized user words."}
```

It submits its authored text with those exact three identities:

```json
{"type":"reply","session_id":"SESSION","turn_id":"t1","request_id":"OPAQUE ID","text":"The application's complete response."}
```

`reply_accepted` acknowledges **admission**, not completed inference or playback.
`reply_refused` names a stale, duplicate or invalid reply; no such reply speaks.
Whitespace-only text, unsupported control characters, more than 8,000 characters
or an indivisible token exceeding the 180-character segment bound are refused
before acceptance. Text is segmented losslessly; model token-cap exhaustion is
failure, not a complete response. One current reply identity is live. New speech,
abort or close invalidates obsolete work even if an application sends it late.

| Command | Admission and eventual outcome |
| --- | --- |
| `{"type":"reply",...}` | Returns admission/refusal without awaiting inference. An unanswered request fails the session after 30 seconds, including while input is idle. |
| `{"type":"interrupt"}` | Fences synthesis delivery and requests native stop independently of the inference worker. Cleanup can settle later; it must settle before model reuse. This local convenience operation coordinates both actions; the component's cancel and host stop remain distinct owners. |
| `{"type":"end"}` | Half-closes input without waiting for space in the model queue. Native `input_closed` states the exact capture cutoff; already admitted input, final transcription, eligible application reply and playback drain remain outstanding. |
| `{"type":"status"}` | Returns an immediate snapshot: input open/closed, recognition idle/recognizing, application awaiting_reply/idle, synthesis component state, playback state/IDs, draining, admitted and processed samples. It is not a native playback receipt or a terminal event. |
| WebSocket close | Aborts owned work, invalidates responses and persists cancellation evidence. It is not graceful completion. A genuinely stuck native inference call still needs process-supervisor termination; this interface does not claim force-kill of a Python thread. |

Application waiting and synthesis are outside the input-consumer task. Notifications
use a bounded writer, so a slow network send does not sit in front of native stop.
Queue exhaustion and progress deadlines are explicit failures, not silent drops.
The component has bounded PCM buffering; native output uses playback credit and
its own four-second cap. An urgent private FIFO prevents cancel/stop from waiting
for a blocked audio-pipe write. The native receiver retains bounded cancellation
tombstones, including for IDs cancelled before their first PCM arrives.

## Observation and truth boundaries

- `event` wraps canonical transcript, speech, turn and synthesis events.
- `native_playback` wraps host-produced playback start/stop observations. Stop
  reports completion/sample counts; a callback timestamp is not an acoustic
  speaker-stop measurement.
- `synthesis_done.completed` describes model output, not physical completion.
- `complete` follows session drain and evidence finalization. `error` or socket
  loss is not a successful terminal outcome. Evidence remains local.
- In native mode PCM never travels through the browser. Native microphone,
  reference, sample continuity and playback acknowledgements are host-owned.
- Recorded-input integration uses `input_kind: "paced_file"`, binary packets of
  512 microphone float32 samples followed by 512 reference samples at 16 kHz.
  Its `played` delivery credits are client reports. They do not prove DAC drain,
  echo cancellation or physical interruption. Input VAD uses the same pinned
  independent CPU control model as native input, not the TTS executor.

## Reproduce the actual-model application exercise

Use a fresh output directory; the script refuses to overwrite prior evidence:

```sh
"/home/user/work/mlx-quant-sota/.venv-311/bin/python" \
  scripts/run_application_conversation.py \
  --output deliverables/native-conversation/my-real-model-run
```

It supplies paced recorded speech for thirteen turns: ten completed distinct
replies (three segmented paragraphs), three new-speech cancellations and recovery,
and a final input half-close without trailing silence. It retains reply WAVs,
control events, source/model identities and failures. This is an integration
check, not a human panel, independent speech-quality judge or physical-audio test.
The native listening/interruption run and sustained-session gate are separate.
