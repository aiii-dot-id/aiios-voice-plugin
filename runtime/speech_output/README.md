# Application-driven streaming speech output

A standalone application component, not an experiment runner or an AII OS
dependency. Supply a different reply on each call; receive incremental PCM with
bounded buffering, cancellation and explicit completion/failure.

The Python control component is backend-independent. The included executable
backend is currently **macOS Apple Silicon / MLX GPU / English**. This package
does not yet implement the Plugin SDK wire or qualify the other four platforms.

## Run it now on this Mac

From `/home/user/voice-frontier`:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  '/home/user/work/mlx-quant-sota/.venv-311/bin/python' \
  -m runtime.speech_output \
  --text 'Your application supplies this complete reply, including its final words.' \
  --output /tmp/aii-application-reply.wav
```

The output path must not already exist. Audio is written incrementally as mono
24 kHz PCM16 WAV. Ctrl-C fences further output; partial audio is retained and
the command exits unsuccessfully rather than claiming a completed reply.
It does not open microphones, play through speakers or change device routing.
Backend diagnostic messages precede the final JSON status; stdout is not a
JSON-RPC transport. The model loads once per CLI invocation; embed the API for
a resident application.

The backend verifies the existing local Qwen3-TTS manifest and files before
loading. It does not download weights. This workspace currently uses MLX 0.32.2,
mlx-audio 0.4.7 and NumPy 2.4.6. Model revision and manifest hash are returned with
the result. Distribute models/dependencies under their own applicable terms.

## Embed in an application

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from runtime.speech_output import SpeechOutput
from runtime.speech_output.mlx_backend import MLXTTSBackend

async def speak_application_reply(reply_text, consume_pcm):
    # A resident application keeps this executor/backend/service open across
    # replies. Load and execute a backend on one dedicated owner thread.
    with ThreadPoolExecutor(max_workers=1) as executor:
        loop = asyncio.get_running_loop()
        backend = await loop.run_in_executor(
            executor, MLXTTSBackend,
            Path('/home/user/voice-frontier'),
        )
        speech = SpeechOutput(backend, executor)
        try:
            job = speech.submit(reply_text)  # immediate admission; no inference
            while (chunk := await job.read()) is not None:
                await consume_pcm(chunk)  # bounded host endpoint, not an unbounded list
            return await job.wait()
        finally:
            await speech.close(abort=True)
```

In a resident integration, hold `job` for cancellation from the same event
loop. `speech.cancel(job)` returns immediately, clears the component's queued
PCM and refuses any result from the in-flight model call. `await job.wait()`
settles cleanup; only then may another reply use this model instance. A blocked
native call cannot be forcibly killed by Python; an embedding supervisor owns
that timeout/process-kill policy. Do not cancel the private worker task as an
interruption mechanism.

**Stopping synthesis is not stopping speakers.** On user interruption the host
must independently stop/flush its playback endpoint and reject that synthesis ID
before or alongside `cancel`. PCM already handed to a host is outside this
component's queue. The current API deliberately reports
`playback_state="host_owned_not_observed"`; it cannot certify acoustic silence.

## Contract

| Operation or state | Meaning |
| --- | --- |
| `submit(text)` | Immediate admission with a unique synthesis ID; rejects invalid text or a running/draining predecessor |
| `job.read()` | Single async consumer receives read-only float32 mono PCM, sequence and contiguous sample offset; `None` ends delivery |
| `cancel(job)` | Immediate delivery fence, not completion of an in-flight inference call |
| `job.wait()` | Waits for generation and generator cleanup; raises on failure |
| `job.snapshot()` | Nonblocking state and generated/delivered/queued sample counts |
| `draining` | Generation ended, but this component still holds PCM |
| `completed` | Natural model termination and all component PCM handed off; **not** physical playback completion |
| `close(abort=False)` | Waits for generation; caller must drain concurrently; refuses already queued audio without a consumer |
| `close(abort=True)` | Fences output then waits for model cleanup |

All control calls use one asyncio loop. Only one synthesis may own a backend.
Keep all access to that model on its dedicated executor; do not share it with
an unrelated uncoordinated model caller. A slow/failed consumer causes a visible
timeout (15 seconds default), not unbounded memory growth or silent truncation.

Default queue cap is two seconds of PCM, plus one backend chunk of at most five
seconds and model-internal buffers. Text is limited to 8,000 characters, split
at sentence/word boundaries into at most 180-character segments by default.
The exact partition preserves all input characters. An unbroken word longer
than the segment limit is refused. The backend strips only segment-edge
whitespace; no text content is dropped. Sentence splitting is not a prosody or
multilingual-quality guarantee; cross-segment voice consistency needs listening.

Empty output, invalid audio, generator errors and hitting the backend's token
cap are failures, never successful short replies. Already delivered partial
speech cannot be recalled; the application must handle the failed terminal.

## Measured status

See [the component assessment](../../deliverables/speech-output/README.md).
The software flow works with the real local models. One internal word mismatch
keeps the strict three-case TTS/STT word gate red. Listening quality, physical
interruption in this new API, full-session and Plugin SDK integration, voice
consistency across segments and complete five-platform packaging remain concrete
integration/quality work; they are not inferred from unit tests.
