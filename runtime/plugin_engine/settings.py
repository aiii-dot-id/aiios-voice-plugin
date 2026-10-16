"""Bounded read-only host settings bridge over the carrier's private pipe."""

import asyncio


class SettingsReader:
    def __init__(self, emit):
        self.emit = emit
        self.next_id = 0
        self.pending = None
        self.retired = {}

    async def load(self, session_id):
        if self.pending is not None:
            raise RuntimeError("settings read already pending")
        self.next_id += 1
        request_id = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending = (request_id, session_id, future)
        try:
            self.emit(
                {"settings_request": {"id": request_id, "session_id": session_id}}
            )
            async with asyncio.timeout(2):
                return await future
        finally:
            self.pending = None
            self.retired[request_id] = session_id
            while len(self.retired) > 32:
                del self.retired[next(iter(self.retired))]

    def receive(self, body):
        if (
            type(body) is not dict
            or set(body) - {"id", "session_id", "values", "error"}
            or type(body.get("id")) is not int
            or body["id"] <= 0
            or not isinstance(body.get("session_id"), str)
        ):
            raise ValueError("malformed settings reply")
        key, sid = body["id"], body["session_id"]
        if self.pending is None or self.pending[:2] != (key, sid):
            if self.retired.get(key) == sid:
                return  # A retired open can never configure its successor.
            raise ValueError("settings reply does not match an issued read")
        future = self.pending[2]
        if future.cancelled():
            return  # Cancellation can precede the owner's finally block.
        if future.done():
            raise ValueError("duplicate settings reply")
        if "error" in body:
            if (
                "values" in body
                or not isinstance(body["error"], str)
                or not body["error"]
            ):
                raise ValueError("ambiguous settings outcome")
            future.set_exception(RuntimeError("host settings read failed"))
        elif type(body.get("values")) is not dict:
            raise ValueError("settings values must be an object")
        else:
            future.set_result(body["values"])
