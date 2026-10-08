"""Exercise post-turn WAV recording through the native worker and a strict fake host.

Synthetic PCM only. This is not an installed AII OS, browser, microphone, or
human-speaker qualification. The Go carrier's descriptor/argument contracts
are separately tested by plugin/native and prove_plugin_sdk_engine.py.
"""
from scripts._assertions import require_assertions
require_assertions()

import base64
import hashlib
import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from runtime.plugin_engine.audio import END, PCM, Frame

ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(os.environ.get("AII_TEST_WAVEFORM_WORKER", ROOT / ".build-waveform/aii_voice_worker_fixture"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def run():
    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    env = {**os.environ, "AII_AUDIO_IN_FD": str(input_read), "AII_AUDIO_OUT_FD": str(output_write)}
    process = subprocess.Popen(
        [str(WORKER), *["fixture-path"] * 7], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        pass_fds=(input_read, output_write), bufsize=0,
    )
    os.close(input_read)
    os.close(output_write)
    replies = queue.Queue()
    events = queue.Queue()
    storage = {}
    broker_calls = []
    fault = {"stage_once": False}
    errors = queue.Queue()
    lock = threading.Lock()

    def send(message):
        with lock:
            process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
            process.stdin.flush()

    def broker(query):
        q = query["snapshot_request"]
        assert q["resource"].startswith("waveform:")
        ident = q["resource"].split(":", 1)[1]
        assert len(ident) == 64 and all(c in "0123456789abcdef" for c in ident)
        target = "recordings/" + ident + ".wav"
        stage = "recordings/." + q.get("upload", "") + ".pending"
        action = q.get("action", "read")
        path = stage if action == "stage" else target
        if action == "stage":
            if fault["stage_once"]:
                fault["stage_once"] = False
                send({"snapshot_reply": {"id": q["id"], "session_id": q["session_id"],
                                         "error": "private write refused"}})
                return
            part = base64.b64decode(q["data_b64"], validate=True)
            assert 0 < len(part) <= 65536 and q["upload"] == ident
            assert q.get("append", False) == (stage in storage)
            storage[stage] = storage.get(stage, b"") + part
            result = {"root": "private", "path": stage, "bytes": len(part),
                      "size": len(storage[stage]), "appended": q.get("append", False)}
        elif action == "publish":
            assert q["expected_absent"] is True and q["upload"] == ident
            assert q["sha256"] == sha(storage[stage]) and target not in storage
            storage[target] = storage.pop(stage)
            result = {"root": "private", "path": target, "size": len(storage[target]),
                      "sha256": sha(storage[target]), "replaced": False,
                      "durable": True, "durability": "synced"}
        else:
            assert path in storage and q["offset"] <= len(storage[path])
            raw = storage[path]
            part = raw[q["offset"]:q["offset"] + 65536]
            result = {"root": "private", "path": path, "offset": q["offset"],
                      "size": len(raw), "bytes": len(part),
                      "eof": q["offset"] + len(part) == len(raw),
                      "data_b64": base64.b64encode(part).decode()}
            if q["digest"]:
                result["sha256"] = sha(raw)
        broker_calls.append(action)
        send({"snapshot_reply": {"id": q["id"], "session_id": q["session_id"],
                                 "value": result}})

    def reader():
        try:
            for line in process.stdout:
                message = json.loads(line)
                if "snapshot_request" in message:
                    broker(message)
                elif "settings_request" in message:
                    q = message["settings_request"]
                    send({"settings_reply": {"id": q["id"],
                                              "session_id": q["session_id"], "values": {}}})
                elif "event" in message:
                    events.put(message["event"])
                elif "id" in message:
                    replies.put(message)
                elif "ready" not in message:
                    raise AssertionError("unknown private worker message")
        except BaseException as exc:
            errors.put(exc)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    audio = os.fdopen(input_write, "wb", buffering=0)
    output = os.fdopen(output_read, "rb", buffering=0)
    try:
        request = 0

        def call(operation, arguments, *, refuse=False):
            nonlocal request
            request += 1
            send({"id": request, "operation": operation, "arguments": arguments})
            reply = replies.get(timeout=10)
            assert reply["id"] == request, reply
            assert ("error" in reply) is refuse, reply
            return reply

        def event(kind):
            end = time.monotonic() + 10
            while time.monotonic() < end:
                if not errors.empty():
                    raise errors.get()
                try:
                    row = events.get(timeout=.1)
                except queue.Empty:
                    continue
                if row["type"] == "failure" and kind != "failure":
                    raise AssertionError(row)
                if row["type"] == kind:
                    return row
            raise TimeoutError(kind)

        call("recording.record", {}, refuse=True)  # no microphone audio exists
        opened = call("speech.session.open", {"session_id": "synthetic-recording",
            "input_handle": "mic", "output_handle": "speaker",
            "audio": {"format": "s16le", "input": {"rate": 16000, "channels": 1},
                      "output": {"rate": 24000, "channels": 1}}})
        assert opened["result"]["audio"]["input"]["rate"] == 16000
        event("session_ready")
        seq = 0
        position = 0

        def frames(samples, value):
            nonlocal seq, position
            while samples:
                count = min(1024, samples)
                seq += 1
                audio.write(Frame(PCM, 9, seq, position,
                                  struct.pack("<h", value) * count).encode())
                position += count
                samples -= count

        frames(4096, 1234)  # Admitted before the identity asks to save the turn.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            seen = call("speech.session.status", {"session_id": "synthetic-recording"})
            if seen["result"]["input"]["received_end_sample"] == position:
                break
            time.sleep(.02)
        else:
            raise AssertionError("pre-turn PCM was not admitted")
        # This is the case a real session hit: PCM exists, but STT has not finalized it.
        first = call("recording.record", {})["result"]["operation_result"]
        assert first["state"] == "saved" and first["samples"] == 4096, first
        assert sha(storage[first["private_path"]]) == first["sha256"]
        frames(48000, 8192)
        call("speech.session.finish_input", {"session_id": "synthetic-recording",
            "stream_id": "mic", "end_sample": position})
        seq += 1
        audio.write(Frame(END, 9, seq, position, b"").encode())
        final = event("transcript_final")
        assert final["end_sample"] == position, final
        call("speech.session.close", {"session_id": "synthetic-recording", "mode": "drain"})
        event("session_end")
        fault["stage_once"] = True
        call("recording.record", {}, refuse=True)
        assert call("recording.status", {})["result"]["operation_result"]["state"] == "failed"
        state = call("recording.record", {})["result"]["operation_result"]
        assert state["state"] == "saved", state
        assert state["samples"] == 48000 and state["private_path"].startswith("recordings/")
        assert call("recording.status", {})["result"]["operation_result"] == state
        assert "data_b64" not in state and "audio" not in state
        assert broker_calls.count("stage") == 3 and broker_calls.count("publish") == 2
        wav = storage[state["private_path"]]
        assert sha(wav) == state["sha256"] and len(wav) == 44 + state["samples"] * 2
        assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
        assert struct.unpack_from("<H", wav, 44)[0] in (1234, 8192)
        assert struct.unpack_from("<H", wav, -2)[0] == 8192
        call("recording.record", {}, refuse=True)  # saved buffer cannot be replayed
        # A failed speech session is retired, not a permanent poisoned worker.
        call("speech.session.open", {"session_id": "synthetic-failure",
            "input_handle": "mic-failure", "output_handle": "speaker-failure",
            "audio": {"format": "s16le", "input": {"rate": 16000, "channels": 1},
                      "output": {"rate": 24000, "channels": 1}}})
        event("session_ready")
        audio.write(Frame(PCM, 10, 1, 0, struct.pack("<h", 500) * 8000).encode())
        call("speech.session.finish_input", {"session_id": "synthetic-failure",
            "stream_id": "mic-failure", "end_sample": 16000})
        failed = event("failure")
        assert failed["resources_released"] is True, failed
        reopened = call("speech.session.open", {"session_id": "synthetic-recovered",
            "input_handle": "mic-recovered", "output_handle": "speaker-recovered",
            "audio": {"format": "s16le", "input": {"rate": 16000, "channels": 1},
                      "output": {"rate": 24000, "channels": 1}}})
        assert reopened["result"]["session_id"] == "synthetic-recovered", reopened
        event("session_ready")
        call("speech.session.close", {"session_id": "synthetic-recovered", "mode": "abort"})
        event("session_end")
        print(json.dumps({"passed": True, "scope": "synthetic worker/private-broker",
                          "samples": state["samples"], "upload_pages": 3,
                          "readback_verified": True, "post_turn": True,
                          "post_session": True, "before_stt_final": True,
                          "retry_after_refused_storage": True,
                          "reopen_after_failed_session": True,
                          "record_call_returns_saved_file": True,
                          "no_extra_confirmation": True}))
    finally:
        audio.close()
        process.stdin.close()
        try:
            process.wait(timeout=7)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        output.close()
        thread.join(timeout=2)
        if not errors.empty():
            raise errors.get()
        assert process.returncode == 0, process.stderr.read().decode(errors="replace")
        process.stderr.close()


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    run()
