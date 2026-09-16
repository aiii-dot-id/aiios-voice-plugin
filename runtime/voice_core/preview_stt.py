"""Fast initial previews, then original-context confirmation; shared weights."""

from types import SimpleNamespace

from runtime.stt.nemotron_push import NemotronPushStream


class PreviewSTT:
    def __init__(self, model, preview_context=0, *, language="en-US"):
        # Settings admission validates the model's supported set; refuse an
        # unknown prompt again here before upstream can silently use "auto".
        prompts = getattr(model, "prompt_dictionary", None)
        if (
            not isinstance(language, str)
            or not language
            or (prompts is not None and language not in prompts)
        ):
            raise ValueError("unsupported recognition language")
        self.closed = False
        self.handoff_samples = None
        self.preview_samples = 0
        self.last_confirm = SimpleNamespace(result=SimpleNamespace(text=""))
        self.confirm = NemotronPushStream(
            model, language=language, att_context_size=[56, 6], max_audio_seconds=60
        )
        self.preview = (
            NemotronPushStream(
                model,
                language=language,
                att_context_size=[56, preview_context],
                max_audio_seconds=60,
            )
            if preview_context != 6
            else self.confirm
        )

    def push_audio(self, samples):
        if self.closed:
            raise RuntimeError("cannot push after confirmed final")
        if self.preview is None:
            updates = self.confirm.push_audio(samples)
            if updates:
                self.last_confirm = updates[-1]
            return updates
        updates = self.preview.push_audio(samples)
        confirmed = updates
        if self.preview is not self.confirm:
            confirmed = self.confirm.push_audio(samples)
            self.preview_samples += len(samples)
        if confirmed:
            self.last_confirm = confirmed[-1]
        # Preserve the fast update on this chunk and the confirmation cache.
        # Later chunks go only to confirmation: never replay the prefix.
        if self.preview is not self.confirm and self.last_confirm.result.text.strip():
            self.handoff_samples = self.confirm.samples_admitted
            self.preview = None
        return updates

    def finish(self):
        if self.closed:
            raise RuntimeError("duplicate confirmed final")
        self.closed = True
        return self.confirm.finish() or [self.last_confirm]
