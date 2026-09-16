# AII Voice — standalone application

This connects the existing native speech engine to a configured local conversation
model. Manual replies remain available: the speech platform has no mandatory
reasoning-LLM or AII OS dependency. Optional speaker tools use the existing UID
component. There is no duplicated audio stack or new learned checkpoint.

## Run and use

From `/home/user/voice-frontier`:

```sh
'/home/user/work/mlx-quant-sota/.venv-311/bin/python' \
  -m runtime.voice_app --config configs/local-voice.json
```

Open **http://127.0.0.1:9139/**. List devices, confirm microphone/speakers and Start.
Ask a question, allow a reply, interrupt naturally and continue. **Finish & drain**
closes input while preserving the final transcript/reply. **Abort** cancels the
owned session. Closing/reloading the page aborts, not successfully drains.
No capture occurs on startup, health checks, device listing or speaker operations.

The current installation reuses the running engine on **9138**, pinned to source
`e4ec0de1b1e69834ec11cdb95327d9338e58ffae64d60359a0263e6f405151d4`.
Its STT/TTS models and native host are unchanged. The frozen reference on **9137**
is untouched. One session owns the engine: finish/abort another page first.
Conflicting sessions are refused rather than displaced. System audio defaults
are not changed; the existing native host privately selects the requested devices.

`configs/local-voice.json` configures the application port, voice URL/source,
conversation endpoint/model/deadline/token cap, evidence root and optional UID
paths. This installation uses absolute paths. Endpoints are explicit loopback
HTTP: this app does not send speech/transcripts to a cloud provider. For an
authenticated local provider, set `chat.api_key_env` to an environment-variable
name; the value stays out of config, browser and evidence.

The current local example uses the already resident
`GLM-5.2-Alis-MLX-Dynamic-4.5bpw` at port 8081. This is **not** a requirement to ship
that large model with the voice platform. Exact residency and memory pressure are
checked before each actual inference request. The app never calls load/unload
endpoints or changes defaults/settings. `/health` establishes speech-engine
readiness; conversation readiness is checked separately at reply admission.

## Application contract

- The existing voice engine owns audio, VAD, STT, turn commitment, synthesis,
  cancellation, playback, cleanup and canonical evidence. The application owns
  replies and visible conversational history only. It uses the existing protocol
  in `runtime/voice_core/APPLICATION_SESSION.md`, not a new Plugin SDK wire.
- Model HTTP requests run outside the transport reader. New speech, status,
  interrupt, input finish and abort remain responsive. A new speech epoch fences
  pending results, including callbacks that ignore cancellation. The engine
  independently validates session/turn/request identity before admission.
- Only an answer both fully generated and fully drained enters subsequent
  context as fully heard. Interrupted/undelivered answers are omitted. This is
  conservative whole-answer history, not word-level delivered-text tracking.
- History is bounded to recent turns and 12,000 characters, with per-message
  bounds; it is not durable conversational memory. Replies use the existing
  8,000-character validation and speech segmentation. Provider cleanup is capped
  at four tasks; notification queues at 256 messages.
- The default model deadline is 24 seconds inside the engine's 30-second reply
  deadline. Length-limited, malformed, empty or failed responses are not presented
  as successful complete model answers. The UI reports failure and the app speaks
  an explicitly authored failure notice, keeping the session reusable.
- Cancellation closes/fences local HTTP consumption; server-side GPU cleanup
  belongs to the model server. No remote process-kill claim is made. Generated
  text never executes tools or authorizes a system action.
- Static assets are captured at startup, preventing mixed on-disk frontend
  updates. Restart the idle application after code changes; the speech engine
  need not reload. Host/Origin checks restrict control to the exact loopback page.

## Optional speaker tools

Expand **Optional speaker enrollment**. Supply a chosen ID/name and a 16 kHz mono
PCM16 WAV, 2–30 seconds. Enroll three different recordings, then identify another
recording, list, remove or reset. Listing does not create the database. Removal
and reset remove vectors, not audio/evidence files.

A bounded independent worker loads WeSpeaker only on explicit inference. A busy
worker refuses extra work; it never queues unlimited uploads. Private temporary
upload files are removed after inference. The database stores labels, embeddings
and PCM hashes. Default path: `private/voice-app/speakers.sqlite`. No operator audio
is enrolled automatically. UID absence, ambiguity or failure never gates speech.

**Current boundary:** file-based enrollment/recognition is usable; live automatic
microphone labeling is not connected. The measured policy remains
accuracy-unqualified. Results are development suggestions, not authentication,
authorization, liveness or spoof detection. See the standalone UID README.

## Evidence and remaining product work

Local `application.jsonl` records source/config fingerprints, application
answers/failures and the engine terminal. Canonical audio/transcripts remain in
the engine evidence directory. These contain private conversation and are ignored
by Git; publication needs separate explicit admission. The UI display is bounded.

Tests: `tests/test_voice_application.py` and the existing session/native/output/UID
suites. Actual-model runners: `scripts/prove_voice_application.py` and
`scripts/prove_voice_uid_http.py`. They use public/previously generated recordings,
not an automatic microphone capture. Evidence and failed runs are retained under
`deliverables/voice-application/`.

The product target remains human-level STT/TTS/VAD/UID across macOS, Ubuntu,
Windows, Android and iOS. This is the connected Mac application, not a declaration
that individual model/phone exports satisfy complete device support. Slow or
oververbose local-model replies remain visible limitations of this example.
