import asyncio
from functools import partial

from runtime.voice_core.live import LiveSession


class CUDASession(LiveSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output.backend.max_tokens = self.models.max_tokens

    async def gpu(self, function, *args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(
            self.models.recognition_executor, partial(function, *args, **kwargs)
        )

    async def interrupt(self, reason):
        self.models.cancel_synthesis()
        await super().interrupt(reason)

    def abort(self):
        super().abort()
        self.models.cancel_synthesis()
        self.models.recognizer.cancel()

    async def run(self):
        try:
            await super().run()
        finally:
            await asyncio.get_running_loop().run_in_executor(
                self.models.recognition_executor, self.models.recognizer.retire
            )
