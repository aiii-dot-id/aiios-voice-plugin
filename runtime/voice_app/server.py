"""Local application controller. Existing voice engine owns audio and evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
import uuid
import wave
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

from .conversation import Conversation, LocalChat, bounded_json
from .uid import SpeakerTools

ROOT = Path(__file__).resolve().parent


def validate_config(config):
    if type(config.get("port")) is not int or not 1024 <= config["port"] <= 65535:
        raise ValueError("application port must be within 1024..65535")
    urls = [config["voice_url"], config["chat"]["url"]]
    if config["chat"].get("resident_check_url"):
        urls.append(config["chat"]["resident_check_url"])
    for url in urls:
        parsed = urlparse(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError(
                "this local application requires explicit loopback HTTP endpoints"
            )
    if config["voice_url"].rstrip("/") != config["voice_url"]:
        raise ValueError("voice_url must have no trailing slash")
    if not 1 <= config["chat"].get("timeout_seconds", 24) <= 25:
        raise ValueError(
            "chat deadline must leave time inside the 30 second voice deadline"
        )
    if not isinstance(config["chat"]["model"], str) or not config["chat"]["model"]:
        raise ValueError("an explicit conversation model is required")
    if not 16 <= config["chat"].get("max_tokens", 192) <= 1024:
        raise ValueError("conversation token cap must be within 16..1024")
    if len(config["voice_source_sha256"]) != 64:
        raise ValueError("pin the already validated voice source")
    if any(k in config for k in ("api_key", "token")) or "api_key" in config["chat"]:
        raise ValueError("use chat.api_key_env; do not store credentials in this file")


def create_app(config, *, provider_factory=LocalChat, speaker_tools=None):
    validate_config(config)
    origin = f"http://127.0.0.1:{config['port']}"
    assets = {p.name: p.read_bytes() for p in (ROOT / "web").iterdir() if p.is_file()}
    source_files = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(ROOT.rglob("*"))
        if p.is_file() and p.suffix in {".py", ".js", ".css", ".html"}
    }
    source_sha = hashlib.sha256(
        json.dumps(source_files, sort_keys=True).encode()
    ).hexdigest()
    config_sha = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    speakers = speaker_tools or (
        SpeakerTools(config["uid"]) if config.get("uid") else None
    )
    active = False
    client = None
    live_sockets = set()

    @web.middleware
    async def local(request, handler):
        if request.host != origin.removeprefix("http://"):
            raise web.HTTPForbidden(text="loopback host required")
        supplied = request.headers.get("Origin")
        if supplied not in (None, origin) or (
            request.method != "GET" and supplied != origin
        ):
            raise web.HTTPForbidden(text="same-origin control required")
        result = await handler(request)
        result.headers["Cache-Control"] = "no-store"
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
        )
        return result

    app = web.Application(middlewares=[local], client_max_size=1025536)

    async def resources(_):
        nonlocal client
        async with aiohttp.ClientSession() as client:
            yield

    async def shutdown(_):
        for ws in list(live_sockets):
            await ws.close(code=1001, message=b"application shutdown")

    app.cleanup_ctx.append(resources)
    app.on_shutdown.append(shutdown)

    async def voice_json(path):
        async with client.get(
            config["voice_url"] + path,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"voice engine HTTP {response.status}")
            return await bounded_json(response)

    async def check_voice():
        state = await voice_json("/health")
        if (
            state.get("status") != "ready"
            or not state.get("application_replies")
            or state.get("source_sha256") != config["voice_source_sha256"]
        ):
            raise RuntimeError("voice engine readiness or pinned source differs")
        return state

    async def health(_):
        try:
            voice = await check_voice()
            state = "ready"
        except (RuntimeError, aiohttp.ClientError, TimeoutError):
            voice, state = {}, "voice_unavailable"
        return web.json_response(
            {
                "status": state,
                "busy": active,
                "voice_busy": voice.get("busy"),
                "source_sha256": source_sha,
                "config_sha256": config_sha,
                "voice_source_sha256": voice.get("source_sha256"),
                "model": config["chat"]["model"],
                "conversation_provider": "local",
                "uid": "optional_development_tools" if speakers else "disabled",
                "audio_owner": "existing_voice_engine",
                "microphone_started": False if not active else None,
            }
        )

    async def devices(_):
        await check_voice()
        return web.json_response(await voice_json("/native-devices"))

    async def uid(request):
        if not speakers:
            raise web.HTTPNotFound()
        operation = request.match_info["operation"]
        try:
            if request.method == "GET":
                if operation != "list":
                    raise web.HTTPMethodNotAllowed(request.method, ["POST"])
                options = {}
            elif operation in {"enroll", "identify"}:
                options = {
                    "audio": await request.read(),
                    "speaker_id": request.query.get("speaker_id"),
                    "label": request.query.get("label"),
                }
            else:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("speaker control must be an object")
                if operation == "reset" and body.get("confirm") is not True:
                    raise ValueError("reset needs explicit confirmation")
                options = {"speaker_id": body.get("speaker_id")}
            return web.json_response(await speakers.call(operation, **options))
        except BlockingIOError as error:
            return web.json_response({"error": str(error)}, status=409)
        except (
            ValueError,
            TypeError,
            KeyError,
            OSError,
            RuntimeError,
            sqlite3.Error,
            wave.Error,
            EOFError,
        ) as error:
            return web.json_response({"error": str(error)}, status=400)

    async def asset(request):
        name = request.match_info.get("name", "index.html")
        if name not in assets:
            raise web.HTTPNotFound()
        types = {"html": "text/html", "css": "text/css", "js": "application/javascript"}
        return web.Response(
            body=assets[name], content_type=types[name.rsplit(".", 1)[-1]]
        )

    async def socket(request):
        nonlocal active
        if request.headers.get("Origin") != origin:
            raise web.HTTPForbidden(text="same-origin session required")
        if active:
            raise web.HTTPConflict(text="application already has an active session")
        active = True
        ws = web.WebSocketResponse(max_msg_size=65536, heartbeat=20)
        voice = conversation = journal = None
        jobs = []
        queue = asyncio.Queue(maxsize=256)
        terminal = False

        async def notify(row):
            if row.get("type", "").startswith("application_"):
                journal.write(
                    json.dumps({"monotonic_ns": time.monotonic_ns(), **row}) + "\n"
                )
            queue.put_nowait(row)  # Refuse a slow observer, never an unbounded backlog.

        async def writer():
            while True:
                row = await queue.get()
                try:
                    await asyncio.wait_for(ws.send_json(row), 5)
                finally:
                    queue.task_done()

        async def read_voice():
            nonlocal terminal
            async for message in voice:
                if message.type != aiohttp.WSMsgType.TEXT:
                    break
                row = json.loads(message.data)
                await conversation.observe(row)
                # Full canonical evidence stays with the engine. Per-chunk VAD
                # is not useful UI traffic. Audio credits still pass in file mode.
                if (
                    row.get("type") != "event"
                    or row.get("event", {}).get("type") != "vad_probability"
                ):
                    await notify(row)
                if row.get("type") in {"complete", "error"}:
                    terminal = True
                    journal.write(json.dumps({"type": "voice_terminal", **row}) + "\n")
            if not terminal:
                raise RuntimeError("voice engine disconnected without completion")

        async def read_user():
            async for message in ws:
                if message.type == aiohttp.WSMsgType.BINARY:
                    await voice.send_bytes(
                        message.data
                    )  # Engine rejects this in native mode.
                elif message.type == aiohttp.WSMsgType.TEXT:
                    body = json.loads(message.data)
                    if not isinstance(body, dict):
                        raise ValueError("control must be an object")
                    command = body.get("type")
                    if command == "abort":
                        conversation.invalidate()
                        return
                    if command == "reply":
                        try:
                            await conversation.manual(body)
                        except (ValueError, TypeError) as error:
                            await notify(
                                {
                                    "type": "reply_refused",
                                    "request_id": body.get("request_id"),
                                    "reason": str(error),
                                }
                            )
                    elif command in {
                        "interrupt",
                        "end",
                        "status",
                        "played",
                        "playback_start",
                        "playback_stop",
                    }:
                        if command == "interrupt":
                            conversation.invalidate()
                        await voice.send_json(body)
                    else:
                        raise ValueError("unsupported application control")
                else:
                    return

        try:
            state = await check_voice()
            if state.get("busy"):
                raise web.HTTPConflict(
                    text="voice engine is already in use; not touched"
                )
            await ws.prepare(request)
            live_sockets.add(ws)
            first = await ws.receive_json(timeout=15)
            if not isinstance(first, dict) or first.get("type") != "start":
                raise ValueError("first message must start a session")
            mode = first.pop("application_mode", "conversation")
            if mode not in {"conversation", "manual"}:
                raise ValueError("choose conversation or manual mode")
            first["reply_mode"] = "application"
            first["reply"] = "Application-controlled speech."
            directory = Path(config["evidence_root"]) / uuid.uuid4().hex
            directory.mkdir(parents=True, mode=0o700)
            journal = (directory / "application.jsonl").open("x", buffering=1)
            journal.write(
                json.dumps(
                    {
                        "type": "application_start",
                        "source_sha256": source_sha,
                        "source_files": source_files,
                        "config_sha256": config_sha,
                        "mode": mode,
                        "voice_health": state,
                    }
                )
                + "\n"
            )
            voice = await client.ws_connect(
                config["voice_url"] + "/ws",
                origin=config["voice_url"],
                max_msg_size=2**20,
                timeout=aiohttp.ClientWSTimeout(ws_close=2),
            )
            key = os.environ.get(config["chat"].get("api_key_env", ""))
            conversation = Conversation(
                provider_factory(client, config["chat"], api_key=key),
                voice.send_json,
                notify,
                mode=mode,
            )
            await voice.send_json(first)
            jobs = [
                asyncio.create_task(writer()),
                asyncio.create_task(read_voice()),
                asyncio.create_task(read_user()),
            ]
            done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
            for job in done:
                await job
            if terminal:
                await asyncio.wait_for(queue.join(), 5)
        except web.HTTPException:
            raise
        except Exception as error:
            if not ws.prepared:
                raise web.HTTPServiceUnavailable(
                    text="voice engine is unavailable or its source differs"
                ) from error
            if ws.prepared and not ws.closed:
                if jobs:
                    jobs[0].cancel()
                    await asyncio.gather(jobs[0], return_exceptions=True)
                await ws.send_json(
                    {
                        "type": "error",
                        "reason": type(error).__name__ + ": " + str(error)[:160],
                    }
                )
        finally:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            if voice:
                await voice.close()
            if conversation:
                try:
                    await conversation.close()
                except RuntimeError:
                    if journal:
                        journal.write(
                            json.dumps({"type": "application_cleanup_failed"}) + "\n"
                        )
            if journal:
                journal.write(
                    json.dumps(
                        {
                            "type": "application_closed",
                            "voice_terminal_observed": terminal,
                        }
                    )
                    + "\n"
                )
                journal.close()
            if ws.prepared:
                await ws.close()
            live_sockets.discard(ws)
            active = False
        return ws

    app.router.add_get("/health", health)
    app.router.add_get("/native-devices", devices)
    app.router.add_get("/ws", socket)
    app.router.add_get("/uid/{operation}", uid)
    app.router.add_post("/uid/{operation}", uid)
    app.router.add_get("/", asset)
    app.router.add_get("/{name}", asset)
    return app
