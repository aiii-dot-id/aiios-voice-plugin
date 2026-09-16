"""Bounded application replies; inference never owns the transport reader."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

import aiohttp

from runtime.speech_output import split_text

SYSTEM = (
    "You are AII Voice, a helpful local conversational assistant. "
    "Answer naturally in one or two short spoken sentences unless asked for more. "
    "Use plain text, no markdown or reasoning preamble. "
    "You cannot execute tools or actions. Do not claim that you did. "
    "Prior assistant messages in this context were fully played; interrupted "
    "or undelivered answers are deliberately omitted."
)
FAILURE_REPLY = (
    "The local conversation model did not complete a reply. "
    "I am still listening. Please try again, or use manual reply mode next session."
)


class ProviderError(RuntimeError):
    """Safe, bounded diagnostics; never a response body or credential."""


async def bounded_json(response, maximum=256 * 1024):
    data = bytearray()
    async for part in response.content.iter_chunked(8192):
        data.extend(part)
        if len(data) > maximum:
            raise ProviderError("provider response exceeded its byte limit")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as error:
        raise ProviderError("provider did not return valid JSON") from error
    if not isinstance(value, dict):
        raise ProviderError("provider response must be an object")
    return value


class LocalChat:
    def __init__(self, client, config, *, api_key=None):
        self.client, self.config = client, config
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def check_resident(self):
        url = self.config.get("resident_check_url")
        if not url:
            return
        async with self.client.get(
            url,
            headers=self.headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=3),
        ) as response:
            if response.status != 200:
                raise ProviderError(f"resident check HTTP {response.status}")
            state = await bounded_json(response, 2 * 1024 * 1024)
        rows = [
            r for r in state.get("models", []) if r.get("id") == self.config["model"]
        ]
        if len(rows) != 1 or not rows[0].get("loaded") or rows[0].get("is_loading"):
            raise ProviderError("configured conversation model is not already resident")
        if state.get("memory", {}).get("pressure_level") not in (None, "ok"):
            raise ProviderError("conversation model memory pressure is not healthy")

    async def reply(self, messages):
        # Includes preflight in the deadline. This only observes residency; it
        # never selects an alias/default or calls a model load/unload endpoint.
        async with asyncio.timeout(self.config.get("timeout_seconds", 24)):
            await self.check_resident()
            payload = {
                "model": self.config["model"],
                "messages": [{"role": "system", "content": SYSTEM}, *messages],
                "max_tokens": self.config.get("max_tokens", 192),
                "temperature": 0.3,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            async with self.client.post(
                self.config["url"],
                json=payload,
                headers=self.headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=None),
            ) as response:
                if response.status != 200:
                    raise ProviderError(f"conversation provider HTTP {response.status}")
                result = await bounded_json(response)
            choices = result.get("choices")
            if not isinstance(choices, list) or len(choices) != 1:
                raise ProviderError("conversation provider needs one completed answer")
            answer = choices[0]
            if not isinstance(answer, dict) or not isinstance(
                answer.get("message"), dict
            ):
                raise ProviderError(
                    "conversation provider returned an invalid answer shape"
                )
            if answer.get("finish_reason") != "stop":
                raise ProviderError("conversation provider did not finish the answer")
            text = answer.get("message", {}).get("content")
            try:
                split_text(text)
            except (ValueError, TypeError) as error:
                raise ProviderError(
                    "conversation provider returned unspeakable text"
                ) from error
            return text.strip()


@dataclass
class Turn:
    request: dict
    answer: str | None = None
    accepted: bool = False
    generated: bool = False
    drained: bool = False
    interrupted: bool = False
    provider_failed: bool = False


class Conversation:
    def __init__(self, provider, send_voice, notify, *, mode="conversation"):
        self.provider, self.send_voice, self.notify = provider, send_voice, notify
        self.mode = mode
        self.turns: list[Turn] = []
        self.current = None
        self.tasks = set()
        self.epoch = 0
        self.closed = False

    def invalidate(self):
        self.epoch += 1
        if self.current and not self.current.drained:
            self.current.interrupted = True
        for task in self.tasks:
            task.cancel()

    def messages(self):
        rows = []
        for turn in self.turns[-12:]:
            rows.append({"role": "user", "content": turn.request["text"][:2000]})
            if (
                turn.accepted
                and turn.generated
                and turn.drained
                and not turn.interrupted
                and not turn.provider_failed
            ):
                rows.append({"role": "assistant", "content": turn.answer[:2000]})
        # Whole messages only. Do not silently concatenate truncated histories.
        while sum(len(r["content"]) for r in rows) > 12000 and len(rows) > 1:
            rows.pop(0)
        if rows and rows[0]["role"] == "assistant":
            rows.pop(0)
        return rows

    async def observe(self, row):
        kind, event = row.get("type"), row.get("event", {})
        if kind == "reply_requested":
            if self.closed:
                return
            self.invalidate()
            if len(self.tasks) >= 4:
                raise RuntimeError("application provider cleanup exceeded four tasks")
            turn = Turn(dict(row))
            self.turns.append(turn)
            self.turns = self.turns[-24:]
            self.current = turn
            if self.mode == "conversation":
                task = asyncio.create_task(self.generate(turn, self.epoch))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
        elif kind in {"interrupt", "error"} or (
            kind == "event"
            and event.get("type") in {"speech_start", "interruption_requested"}
        ):
            self.invalidate()
        elif kind in {"reply_accepted", "reply_refused"}:
            for turn in self.turns:
                if turn.request["request_id"] == row.get("request_id"):
                    turn.accepted = kind == "reply_accepted"
                    if not turn.accepted:
                        turn.interrupted = True
        elif kind in {"synthesis_done", "native_playback", "event"}:
            detail = row.get("native", {}) if kind == "native_playback" else event
            sid = row.get("synthesis_id", detail.get("synthesis_id"))
            for turn in self.turns:
                if sid != "s" + turn.request["turn_id"].removeprefix("t"):
                    continue
                if kind == "synthesis_done":
                    turn.generated = row.get("completed") is True
                if detail.get("type") == "playback_stop":
                    turn.drained = detail.get("reason") == "drained"
                    if not turn.drained:
                        turn.interrupted = True

    def live(self, turn, epoch):
        return not self.closed and self.current is turn and epoch == self.epoch

    async def submit(self, turn, text):
        split_text(text)
        turn.answer = text
        await self.send_voice(
            {
                **{k: turn.request[k] for k in ("session_id", "turn_id", "request_id")},
                "type": "reply",
                "text": text,
            }
        )

    async def manual(self, row):
        turn = self.current
        if (
            self.mode != "manual"
            or not turn
            or turn.interrupted
            or any(
                row.get(k) != turn.request[k]
                for k in ("session_id", "turn_id", "request_id")
            )
        ):
            raise ValueError(
                "manual reply does not match a current manual-mode request"
            )
        await self.submit(turn, row.get("text"))

    async def generate(self, turn, epoch):
        started = time.monotonic()
        try:
            await self.notify(
                {"type": "application_thinking", "turn_id": turn.request["turn_id"]}
            )
            try:
                text = await self.provider.reply(self.messages())
            except (ProviderError, TimeoutError, aiohttp.ClientError) as error:
                if not self.live(turn, epoch):
                    return
                turn.provider_failed = True
                await self.notify(
                    {
                        "type": "application_failure",
                        "turn_id": turn.request["turn_id"],
                        "reason": str(error)
                        if isinstance(error, ProviderError)
                        else type(error).__name__,
                    }
                )
                text = (
                    FAILURE_REPLY  # Named failure, never passed off as a model answer.
                )
            if not self.live(turn, epoch):
                return
            await self.submit(turn, text)
            await self.notify(
                {
                    "type": "application_answer",
                    "turn_id": turn.request["turn_id"],
                    "text": text,
                    "provider_failed": turn.provider_failed,
                    "elapsed_seconds": time.monotonic() - started,
                }
            )
        except asyncio.CancelledError:
            pass
        except Exception as error:  # noqa: BLE001 -- own task failures, no silent orphan
            await self.notify(
                {"type": "application_failure", "reason": type(error).__name__}
            )

    async def close(self):
        self.closed = True
        self.invalidate()
        if self.tasks:
            # No unbounded join of a callback that ignores cancellation.
            done, pending = await asyncio.wait(self.tasks, timeout=1)
            for task in done:
                if not task.cancelled():
                    task.exception()
            if pending:
                raise RuntimeError(
                    "application provider did not release after cancellation"
                )
