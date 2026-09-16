"""Real carrier/worker composition with deterministic models; never packaged."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from runtime.plugin_engine.worker import serve
from runtime.voice_core import preview_stt
from tests.test_operator_settings import ConfigurableModels


def decoder(model, **kw):
    return SimpleNamespace(
        push_audio=lambda _: [
            SimpleNamespace(
                result=SimpleNamespace(
                    text=kw["language"] + " cobalt lantern seventeen"
                )
            )
        ],
        finish=lambda: [],
    )


if __name__ == "__main__":
    models = ConfigurableModels()
    models.stt_right_context = 6
    preview_stt.NemotronPushStream = decoder
    with ThreadPoolExecutor(1) as executor, ThreadPoolExecutor(1) as control:
        asyncio.run(serve(models, executor, control))
