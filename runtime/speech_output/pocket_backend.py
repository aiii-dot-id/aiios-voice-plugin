"""Bounded Pocket pipeline for the existing SpeechOutput executor.

Unlike upstream's threaded convenience generator, an owned producer must join
before generator.close() succeeds. Two queued latents bound inference run-ahead.
The model executor decodes one frame per next(); the host fences/stops playback.
The caller must verify the pinned source, model and voice before construction.
"""

import copy
import queue
import threading


class PocketBackend:
    max_tokens = 750

    def __init__(self, model, voice, *, seed=20260908):
        if str(model.device) != "cpu" or model.sample_rate != 24000:
            raise ValueError("This measured Pocket candidate requires CPU and 24 kHz")
        self.model, self.voice, self.seed = model, voice, seed
        self._lock = threading.Lock()
        self._active = None
        self._failed_retirement = False

    def tts_stream(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 512:
            raise ValueError("A nonempty segment of at most 512 characters is required")
        with self._lock:
            if self._failed_retirement:
                raise RuntimeError("Pocket producer retirement failed; model reuse prohibited")
            if self._active is not None:
                raise RuntimeError("Pocket model already has an active stream")
            stream = PocketStream(self, text)
            self._active = stream
            return stream

    @staticmethod
    def tts_next(stream):
        return stream.next()

    def cancel_synthesis(self):
        with self._lock:
            if self._active is not None:
                self._active.cancelled.set()

    def _release(self, stream):
        with self._lock:
            if self._active is stream and not self._failed_retirement:
                self._active = None

    def _chunks(self, text, cancelled):
        from pocket_tts.modules.stateful_module import increment_steps, init_states

        pending = queue.Queue(maxsize=2)
        done = threading.Event()
        failures = []

        def produce():
            import torch

            try:
                with torch.no_grad():
                    for item in self._latents(text, cancelled):
                        while not cancelled.is_set():
                            try:
                                pending.put(item, timeout=0.01)
                                break
                            except queue.Full:
                                continue
                        if cancelled.is_set():
                            break
            except Exception as exc:  # noqa: BLE001 - carry arbitrary model failures to the consumer.
                failures.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=produce, name="pocket-owned-flow", daemon=True)
        worker.start()
        part_id, decoder = None, None
        try:
            while not cancelled.is_set():
                if failures:
                    raise failures[0]
                try:
                    part, latent, capacity, steps = pending.get(timeout=0.01)
                except queue.Empty:
                    if done.is_set():
                        if failures:
                            raise failures[0]
                        return
                    continue
                if part != part_id:
                    decoder = init_states(self.model.mimi, batch_size=1, sequence_length=capacity)
                    part_id = part
                frame = self.model.mimi.decode_from_latent(
                    latent * self.model.flow_lm.emb_std + self.model.flow_lm.emb_mean, decoder)
                increment_steps(self.model.mimi, decoder, increment=steps)
                if cancelled.is_set():
                    return
                yield frame[0, 0].detach().cpu().numpy().copy(), 24000, 1
        finally:
            cancelled.set()
            worker.join(timeout=5)
            if worker.is_alive():
                self._failed_retirement = True
                raise RuntimeError("Pocket producer did not retire; model reuse prohibited")

    def _latents(self, text, cancelled):
        import torch
        from pocket_tts.models.text_chunking import (
            prepare_text_prompt,
            split_into_best_sentences,
        )

        model = self.model
        torch.manual_seed(self.seed)
        parts = split_into_best_sentences(
            model.flow_lm.conditioner.tokenizer, text, 50,
            model.pad_with_spaces_for_short_inputs,
            remove_semicolons=model.remove_semicolons,
            append_terminal_punctuation=model.append_terminal_punctuation,
        )
        for part_index, part in enumerate(parts):
            if cancelled.is_set():
                return
            prepared_text, guessed_tail = prepare_text_prompt(
                part, model.pad_with_spaces_for_short_inputs,
                model.remove_semicolons, model.append_terminal_punctuation,
            )
            tail = model.model_recommended_frames_after_eos
            if tail is None:
                tail = guessed_tail + 2
            state = copy.deepcopy(self.voice)
            prepared = model.flow_lm.conditioner.prepare(prepared_text)
            limit = min(model._estimate_max_gen_len(prepared.shape[1]), self.max_tokens)
            model._expand_kv_cache(state, sequence_length=(
                model._flow_lm_current_end(state) + prepared.shape[1] + limit))
            steps = int(model.mimi.encoder_frame_rate / model.mimi.frame_rate)
            model._run_flow_lm_and_increment_step(model_state=state, text_tokens=prepared)
            latent = torch.full((1, 1, model.flow_lm.ldim), float("nan"),
                                dtype=model.flow_lm.dtype, device=model.device)
            eos_step = None
            for step in range(limit):
                if cancelled.is_set():
                    return
                latent, is_eos = model._run_flow_lm_and_increment_step(
                    model_state=state, backbone_input_latents=latent)
                if is_eos.item() and eos_step is None:
                    eos_step = step
                if eos_step is not None and step >= eos_step + tail:
                    break
                if cancelled.is_set():
                    return
                yield part_index, latent, limit * steps, steps
            else:
                raise RuntimeError("Pocket reached its frame limit without natural EOS")


class PocketStream:
    def __init__(self, owner, text):
        self.owner = owner
        self.cancelled = threading.Event()
        self._running = threading.Lock()
        self._closed = False
        self._generator = owner._chunks(text, self.cancelled)

    def next(self):
        if not self._running.acquire(blocking=False):
            raise RuntimeError("Concurrent Pocket inference refused")
        try:
            if self._closed or self.cancelled.is_set():
                return None
            # no_grad must surround each resume, not remain active across yield:
            # the model executor may also perform unrelated work between frames.
            import torch

            with torch.no_grad():
                result = next(self._generator, None)
            return None if self.cancelled.is_set() else result
        finally:
            self._running.release()

    def close(self):
        self.cancelled.set()
        if not self._running.acquire(blocking=False):
            raise RuntimeError("Retire in-flight Pocket next() before closing")
        try:
            if not self._closed:
                self._generator.close()
                self._closed = True
                self.owner._release(self)
        finally:
            self._running.release()
