"""Private session-to-UID adapter, not a new public SDK operation or event.

The trusted composition root must supply both an explicitly enabled service and
an observation sink. No enrollment, permissions, device access or automatic
speaker-store creation occurs here. The existing recognizer receives unchanged
audio. Multi-speaker/overlap qualification is still required before publishing
these utterance-level candidates as reliable live speaker attribution.
"""

import asyncio
import hashlib
import io
import wave
from collections import deque

from runtime.speaker_identity.backend import MAX_SECONDS, MIN_SAMPLES


class PCMWindow:
    """Exact engine-clock PCM, bounded independently of session duration."""

    def __init__(self, samples=16000 * MAX_SECONDS + 32768):
        self.capacity = samples
        self.end = 0
        self.chunks = deque()

    def append(self, start, pcm):
        if type(start) is not int or start != self.end or len(pcm) % 2:
            raise ValueError("UID PCM must be contiguous, even-sized s16le")
        if pcm:
            self.chunks.append((start, bytes(pcm)))
            self.end += len(pcm) // 2
        floor = max(0, self.end - self.capacity)
        while self.chunks and self.chunks[0][0] + len(self.chunks[0][1]) // 2 <= floor:
            self.chunks.popleft()
        if self.chunks and self.chunks[0][0] < floor:
            begin, chunk = self.chunks.popleft()
            self.chunks.appendleft((floor, chunk[(floor - begin) * 2 :]))

    def span(self, start, end):
        if (
            type(start) is not int
            or type(end) is not int
            or start < max(0, self.end - self.capacity)
            or not start < end <= self.end
        ):
            raise ValueError("UID utterance span is not fully retained")
        pcm = b"".join(
            chunk[max(0, start - begin) * 2 : min(len(chunk), (end - begin) * 2)]
            for begin, chunk in self.chunks
            if begin < end and begin + len(chunk) // 2 > start
        )
        if len(pcm) != (end - start) * 2:
            raise ValueError("UID audio has missing samples")
        return pcm

    def clear(self):
        self.chunks.clear()


class SpeakerSession:
    """One outstanding identification, no inference on the control owner."""

    def __init__(self, session_id, input_handle, tools, observe, changed):
        self.session_id, self.input_handle = session_id, input_handle
        self.tools, self.observe, self.changed = tools, observe, changed
        self.audio = PCMWindow()
        self.task = None
        self.closed = False
        self.last_end = -1
        self.finals = {}  # Bounded span references; no second PCM or embedding store.

    @property
    def pending(self):
        return self.task is not None and not self.task.done()

    def feed(self, start, pcm):
        if not self.closed:
            self.audio.append(start, pcm)
            floor = max(0, self.audio.end - self.audio.capacity)
            self.finals = {
                key: span for key, span in self.finals.items() if span[0] >= floor
            }

    def submit(self, final):
        if self.closed:
            return
        start, end = final.get("start_sample"), final.get("end_sample")
        base = {
            "session_id": self.session_id,
            "input_handle": self.input_handle,
            "utterance_id": final.get("utterance_id"),
            "start_sample": start,
            "end_sample": end,
            "sample_rate": 16000,
            "used_for_permissions": False,
            "qualification": "development_only",
            "diarization_verified": False,
        }
        if "attributes" in final:
            base["attributes"] = final["attributes"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end:
            self.unavailable(base, "invalid_utterance_span")
            return
        if end <= self.last_end:
            return  # A duplicate/stale final cannot trigger another identification.
        self.last_end = end
        if not isinstance(base["utterance_id"], str) or not base["utterance_id"]:
            self.unavailable(base, "missing_utterance_id")
            return
        if not MIN_SAMPLES <= end - start <= MAX_SECONDS * 16000:
            self.unavailable(base, "unsupported_utterance_duration")
            return
        sequence = final.get("attributes")
        if type(sequence) is int and sequence > 0:
            if sequence in self.finals and self.finals[sequence] != (start, end):
                self.unavailable(base, "contradictory_final_sequence")
                return
            self.finals[sequence] = (start, end)
            while len(self.finals) > 128:
                del self.finals[next(iter(self.finals))]
        if self.pending:
            self.unavailable(base, "speaker_worker_busy")
            return
        try:
            pcm = self.audio.span(start, end)
        except ValueError:
            self.unavailable(base, "utterance_audio_not_retained")
            return
        base["audio_sha256"] = hashlib.sha256(pcm).hexdigest()
        self.task = asyncio.create_task(self.identify(base, pcm))

    def selected_recordings(self, session_id, input_handle, sequences):
        """Resolve host-selected finals to this live input's exact retained PCM.

        Authorization is the caller's existing host/tool boundary, not inferred
        from these identifiers. No arbitrary path, span, text or score is input.
        """
        if self.closed or (session_id, input_handle) != (
            self.session_id,
            self.input_handle,
        ):
            raise ValueError("enrollment selection does not belong to the live input")
        if (
            not isinstance(sequences, (list, tuple))
            or len(sequences) != 3
            or any(type(seq) is not int or seq <= 0 for seq in sequences)
            or len(set(sequences)) != 3
        ):
            raise ValueError("select three distinct final sequences")
        try:
            spans = [self.finals[seq] for seq in sequences]
        except KeyError as error:
            raise ValueError("selected final is not retained in this input") from error
        ordered = sorted(spans)
        if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
            raise ValueError("selected recordings overlap")
        recordings, evidence = [], []
        for seq, (start, end) in zip(sequences, spans, strict=True):
            pcm = self.audio.span(start, end)
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(pcm)
            recordings.append(output.getvalue())
            evidence.append(
                dict(
                    attributes=seq,
                    start_sample=start,
                    end_sample=end,
                    sample_rate=16000,
                    pcm_sha256=hashlib.sha256(pcm).hexdigest(),
                )
            )
        if len({row["pcm_sha256"] for row in evidence}) != 3:
            raise ValueError("duplicate decoded recording evidence refused")
        return recordings, evidence

    async def enroll_recordings(
        self, session_id, input_handle, sequences, *, speaker_id, label
    ):
        """Private trusted composition entry; deliberately not a public control."""
        if self.pending:
            raise BlockingIOError("speaker worker is busy; enrollment not started")
        recordings, evidence = self.selected_recordings(
            session_id, input_handle, sequences
        )

        async def enroll():
            try:
                result = await self.tools.enroll_recordings(
                    recordings, speaker_id=speaker_id, label=label
                )
                if result["audio_sha256"] != [row["pcm_sha256"] for row in evidence]:
                    raise ValueError(
                        "enrollment readback names different decoded samples"
                    )
                return {
                    **result,
                    "session_id": session_id,
                    "input_handle": input_handle,
                    "recordings": evidence,
                }
            finally:
                self.changed()

        self.task = asyncio.create_task(enroll())
        # A caller disappearing does not erase an admitted write or free its
        # inference slot. close() retains ownership until this task retires.
        self.task.add_done_callback(
            lambda task: task.exception() if not task.cancelled() else None
        )
        return await asyncio.shield(self.task)

    def unavailable(self, base, reason):
        if not self.closed:
            self.observe(
                {
                    **base,
                    "outcome": "unavailable",
                    "reason": reason,
                    "speaker_id": None,
                    "label": None,
                }
            )

    async def identify(self, base, pcm):
        try:
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(pcm)
            result = await self.tools.call("identify", audio=output.getvalue())
            if result.get("audio_sha256") != base["audio_sha256"]:
                raise ValueError("speaker result names different decoded samples")
            if result.get("outcome") not in {"known", "unknown", "ambiguous"}:
                raise ValueError("speaker result has no supported decision")
            if not self.closed:
                self.observe({**result, **base})
        except Exception as error:  # noqa: BLE001 -- isolate arbitrary native UID failures from speech
            # A UID problem is visible but must not erase speech or stop a reply.
            self.unavailable(base, f"{type(error).__name__}: {error}")
        finally:
            self.changed()

    async def close(self):
        # Fence observations before waiting for the actual native worker. Merely
        # cancelling its asyncio waiter would leave a model call alive on reuse.
        self.closed = True
        self.audio.clear()
        self.finals.clear()
        if self.task is not None:
            # Identification reports its failure as an observation; explicit
            # enrollment reports to its caller. Retirement joins either error
            # without inventing a second failure of the speech session.
            await asyncio.shield(asyncio.gather(self.task, return_exceptions=True))
        await self.tools.retire()
