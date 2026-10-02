"""Bounded per-turn reply admission. No application code runs on the audio path."""

import asyncio
import uuid
from dataclasses import dataclass

from runtime.speech_output import split_text


@dataclass
class ReplyRequest:
    session_id: str
    turn_id: str
    request_id: str
    text: str
    future: asyncio.Future
    invalidated: bool = False
    accepted: bool = False

    def message(self):
        return {
            "type": "reply_requested",
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "text": self.text,
        }


class ApplicationReplies:
    def __init__(self, session_id):
        self.session_id = session_id
        self.current = None
        self.closed = False

    def invalidate(self):
        if self.current:
            self.current.invalidated = True
            if not self.current.future.done():
                self.current.future.set_result(None)

    def request(self, turn_id, text):
        if self.closed:
            raise RuntimeError("application response channel is closed")
        self.invalidate()
        self.current = ReplyRequest(
            self.session_id,
            turn_id,
            uuid.uuid4().hex,
            text,
            asyncio.get_running_loop().create_future(),
        )
        return self.current

    def live(self, request):
        return not self.closed and request is self.current and not request.invalidated

    def submit(self, session_id, turn_id, request_id, text):
        request = self.current
        if (
            not request
            or not self.live(request)
            or (session_id, turn_id, request_id)
            != (request.session_id, request.turn_id, request.request_id)
        ):
            raise ValueError("stale or unknown application reply")
        if request.accepted:
            raise ValueError("duplicate application reply")
        split_text(text)  # Refuse invalid text before acknowledging admission.
        request.accepted = True
        request.future.set_result(text)
        return {"type": "reply_accepted", "request_id": request_id, "turn_id": turn_id}

    def close(self):
        self.invalidate()
        self.closed = True
