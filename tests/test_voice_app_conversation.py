import asyncio

import pytest

from runtime.voice_app.conversation import Conversation, FAILURE_REPLY, LocalChat


@pytest.mark.asyncio
async def test_only_rendered_playback_outcome_decides_assistant_history():
    async def send(_):
        pass

    conversation = Conversation(None, send, send, mode="manual")
    await conversation.observe({
        "type": "reply_requested", "request_id": "r1", "turn_id": "t1",
        "session_id": "session", "text": "First question.",
    })
    conversation.current.answer = "Complete answer."
    await conversation.observe({"type": "reply_accepted", "request_id": "r1"})
    await conversation.observe({
        "type": "synthesis_done", "synthesis_id": "s1", "completed": True,
    })
    await conversation.observe({
        "type": "event", "event": {"type": "playback_stop", "synthesis_id": "s1"},
    })
    assert conversation.messages() == [{"role": "user", "content": "First question."}]
    await conversation.observe({
        "type": "native_playback", "native": {
            "type": "playback_stop", "synthesis_id": "s1", "reason": "drained",
        },
    })
    assert conversation.messages() == [
        {"role": "user", "content": "First question."},
        {"role": "assistant", "content": "Complete answer."},
    ]


@pytest.mark.asyncio
async def test_stopped_playback_never_becomes_assistant_history():
    async def send(_):
        pass

    conversation = Conversation(None, send, send, mode="manual")
    await conversation.observe({
        "type": "reply_requested", "request_id": "r1", "turn_id": "t1",
        "session_id": "session", "text": "First question.",
    })
    conversation.current.answer = "Partly played answer."
    await conversation.observe({"type": "reply_accepted", "request_id": "r1"})
    await conversation.observe({
        "type": "synthesis_done", "synthesis_id": "s1", "completed": True,
    })
    await conversation.observe({
        "type": "event", "event": {"type": "playback_stop", "synthesis_id": "s1"},
    })
    await conversation.observe({
        "type": "native_playback", "native": {
            "type": "playback_stop", "synthesis_id": "s1", "reason": "stopped",
        },
    })
    assert conversation.messages() == [{"role": "user", "content": "First question."}]


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [
    {"models": ["not a model"]},
    {"models": [{"id": "m", "loaded": True}], "memory": None},
    {"models": None},
])
async def test_malformed_resident_check_speaks_failure_without_losing_session(
    monkeypatch, malformed,
):
    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    class Client:
        def get(self, *_args, **_kwargs):
            return Response()

        def post(self, *_args, **_kwargs):
            raise AssertionError("malformed resident status must not call inference")

    async def status_json(*_args, **_kwargs):
        return malformed

    monkeypatch.setattr("runtime.voice_app.conversation.bounded_json", status_json)
    sent, notices = [], []

    async def send(row):
        sent.append(row)

    async def notify(row):
        notices.append(row)

    provider = LocalChat(Client(), {
        "resident_check_url": "http://local/status", "model": "m",
    })
    conversation = Conversation(provider, send, notify)
    await conversation.observe({
        "type": "reply_requested", "request_id": "r1", "turn_id": "t1",
        "session_id": "session", "text": "First question.",
    })
    await asyncio.wait_for(asyncio.gather(*tuple(conversation.tasks)), timeout=1)
    assert len(sent) == 1 and sent[0]["type"] == "reply"
    assert sent[0]["text"] == FAILURE_REPLY
    assert conversation.closed is False
    assert any(row["type"] == "application_failure" for row in notices)
