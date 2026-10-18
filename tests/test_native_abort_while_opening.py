import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from scripts.prove_native_worker_transport import Worker
from tests.native_limits import limits

ABORT_SECONDS = 1.5
ABANDONED = 72


def worker(tmp_path, name, **table):
    return Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / name,
                  limits=limits(abort_ms=int(ABORT_SECONDS * 1000), **table))


def abort_row(w, sid):
    w.counter += 1
    return {"id": w.counter, "operation": "speech.session.close", "arguments": {"session_id": sid, "mode": "abort"}}


def write(w, *rows):
    w.p.stdin.write(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
    w.p.stdin.flush()


def of(w, sid, kind=None):
    return [e for e in w.events if e["session_id"] == sid and (kind is None or e["type"] == kind)]


def test_an_abort_before_the_settings_arrive_ends_the_session_at_once(tmp_path):
    w = worker(tmp_path, "unsettled")
    try:
        query = w.open("early", settings=False)
        assert w.status("early")["lifecycle"] == "opening"
        result, took = w.call("close", session_id="early", mode="abort")
        end = w.event("session_end", "early", timeout=2)
        assert result == {"accepted": True, "mode": "abort"} and took < 1, (result, took)
        assert end["status"] == "aborted", end
        assert w.status("early")["lifecycle"] == "closed"
        w.configure(query, 768)
        w.open("next")
        assert [e["type"] for e in of(w, "early")] == ["session_start", "session_end"], of(w, "early")
        state = w.status("next")
        assert (state["lifecycle"], state["reason"]) == ("open", None), state
        w.call("close", session_id="next", mode="abort")
        assert w.event("session_end", "next")["status"] == "aborted"
        assert not any(e["type"] == "failure" for e in w.events), w.events
    finally:
        assert w.close() == 0


def test_aborts_sent_again_before_the_settings_arrive_end_the_session_once(tmp_path):
    w = worker(tmp_path, "unsettled-again")
    try:
        w.open("early", settings=False)
        rows = [abort_row(w, "early") for _ in range(3)]
        write(w, *rows)
        replies = [w.replies.get(timeout=2) for _ in rows]
        assert [r["id"] for r in replies] == [r["id"] for r in rows], replies
        assert replies[0].get("result") == {"accepted": True, "mode": "abort"}, replies
        assert [r.get("error") for r in replies[1:]] == ["session cannot close in current state"] * 2, replies
        assert [e["status"] for e in of(w, "early", "session_end")] == ["aborted"], of(w, "early")
        ended = next(i for i, row in enumerate(w.all) if row.get("event", {}).get("type") == "session_end")
        refused = next(i for i, row in enumerate(w.all) if row.get("id") == rows[1]["id"])
        assert ended < refused, "the second abort was answered before the session had ended"
        assert w.p.poll() is None, "the engine process ended with its session"
        w.open("next")
        w.call("close", session_id="next", mode="abort")
        w.event("session_end", "next")
    finally:
        assert w.close() == 0


def test_an_abort_right_behind_the_settings_ends_each_session_and_the_engine_stays(tmp_path):
    w = worker(tmp_path, "behind-settings")
    sids = [f"s{index}" for index in range(20)]
    try:
        for sid in sids:
            query = w.open(sid, settings=False)
            row = abort_row(w, sid)
            write(w, {"settings_reply": {**query, "values": {"turn_pause_ms": 768}}}, row)
            reply = w.replies.get(timeout=2)
            assert reply == {"id": row["id"], "result": {"accepted": True, "mode": "abort"}}, reply
            assert w.event("session_end", sid, timeout=3)["status"] == "aborted"
            assert w.p.poll() is None, "the engine process ended with its session"
        assert not any(e["type"] == "failure" for e in w.events), w.events
        assert [sid for sid in sids if not of(w, sid, "session_ready")], "no abort was read before its session's open had returned"
    finally:
        assert w.close() == 0


def will_not_retire(w, sid):
    w.open(sid)
    w.call("synthesize", session_id=sid, synthesis_id="reply", text="Stall.")
    w.event("synthesis_start", sid)
    time.sleep(1)


def never_retired(w, sid):
    assert not of(w, sid, "session_end"), "the session retired: its synthesizer was not yet inside the stalled call"
    assert not of(w, sid, "failure"), of(w, sid)


def exit_after_abort(w, sid, again_every=None, watch=ABORT_SECONDS + 2):
    began = time.monotonic()
    w.call("close", session_id=sid, mode="abort")
    code, again = None, 0
    while code is None and time.monotonic() - began < watch:
        left = began + watch - time.monotonic()
        try:
            code = w.p.wait(timeout=max(0, min(again_every or left, left)))
        except subprocess.TimeoutExpired:
            if again_every and time.monotonic() - began < watch:
                try:
                    w.send(abort_row(w, sid))
                    again += 1
                except OSError:
                    pass
    return code, time.monotonic() - began, again


def put_away(w):
    if w.p.poll() is None:
        w.p.kill()
    w.close()


def test_an_aborted_session_that_will_not_retire_ends_the_process_when_the_aborts_time_is_up(tmp_path):
    w = worker(tmp_path, "one-abort")
    try:
        will_not_retire(w, "s")
        code, after, _ = exit_after_abort(w, "s")
        never_retired(w, "s")
        assert code == ABANDONED, f"exit {code}, {after:.1f} s after the abort"
        assert after >= ABORT_SECONDS - .1, after
    finally:
        put_away(w)


def test_an_abort_sent_again_does_not_move_the_aborts_deadline(tmp_path):
    w = worker(tmp_path, "abort-again")
    try:
        will_not_retire(w, "s")
        code, after, again = exit_after_abort(w, "s", again_every=.4)
        never_retired(w, "s")
        assert code == ABANDONED, (
            f"still running {after:.1f} s after the first abort: each of the {again} aborts sent again moved its deadline")
        assert after >= ABORT_SECONDS - .1, after
    finally:
        put_away(w)


def held_open(tmp_path, monkeypatch, name, **table):
    gate = tmp_path / (name + "-gate")
    gate.mkdir()
    monkeypatch.setenv("AII_FIXTURE_OPEN_GATE", str(gate))
    w = worker(tmp_path, name, **table)
    query = w.open("s", settings=False)
    w.configure(query, 768)
    deadline = time.monotonic() + 5
    while not (gate / "held").exists():
        assert time.monotonic() < deadline, "the open never began"
        time.sleep(.002)
    assert w.status("s")["lifecycle"] == "opening"
    return w, gate


def test_an_aborted_open_that_never_returns_ends_the_process_when_the_aborts_time_is_up(tmp_path, monkeypatch):
    w, _ = held_open(tmp_path, monkeypatch, "hung-aborted")
    try:
        code, after, _ = exit_after_abort(w, "s")
        assert code == ABANDONED, f"exit {code}, {after:.1f} s after the abort: an open that never returned was waited for for ever"
        assert ABORT_SECONDS - .1 <= after < ABORT_SECONDS + 1.5, after
        assert not of(w, "s", "session_end"), "a session whose open never returned was said to have ended cleanly"
    finally:
        put_away(w)


def test_an_abort_sent_again_does_not_move_a_hung_opens_deadline(tmp_path, monkeypatch):
    w, _ = held_open(tmp_path, monkeypatch, "hung-aborted-again")
    try:
        code, after, again = exit_after_abort(w, "s", again_every=.4)
        assert code == ABANDONED and again >= 2, (code, after, again)
        assert ABORT_SECONDS - .1 <= after < ABORT_SECONDS + 1.5, f"{after:.1f} s: each of the {again} aborts sent again moved the deadline"
    finally:
        put_away(w)


def test_an_aborted_open_that_returns_in_the_aborts_time_ends_its_session_and_the_engine_stays(tmp_path, monkeypatch):
    w, gate = held_open(tmp_path, monkeypatch, "slow-aborted")
    try:
        result, _ = w.call("close", session_id="s", mode="abort")
        assert result == {"accepted": True, "mode": "abort"}
        time.sleep(ABORT_SECONDS / 3)
        assert not of(w, "s", "session_end") and w.p.poll() is None
        (gate / "release").write_text("1")
        assert w.event("session_end", "s", timeout=3)["status"] == "aborted"
        assert w.p.poll() is None, "the engine ended with a session whose open came back in time"
        w.open("next")
        w.call("close", session_id="next", mode="abort")
        w.event("session_end", "next")
    finally:
        assert w.close() == 0


def said_failures(w):
    log = (w.out / "stderr.log").read_text()
    return [json.loads(line.removeprefix("AII_VOICE_FAILURE ")) for line in log.splitlines() if line.startswith("AII_VOICE_FAILURE ")]


def test_an_open_nobody_aborts_is_waited_for_its_time_and_then_said_as_the_engines_failure(tmp_path, monkeypatch):
    w, _ = held_open(tmp_path, monkeypatch, "hung", session_open_ms=1000)
    try:
        began = time.monotonic()
        while not said_failures(w):
            assert time.monotonic() - began < 4, "an open that never returned was waited for past its time"
            assert w.p.poll() is None, "the engine ended without saying why"
            time.sleep(.01)
        said = time.monotonic() - began
        assert [f["reason"] for f in said_failures(w)] == ["session open did not return in 1 seconds"], said_failures(w)
        assert .5 <= said < 3, said
        code = w.p.wait(timeout=ABORT_SECONDS + 2)
        ended = time.monotonic() - began
        assert code == ABANDONED, code
        assert ended - said >= ABORT_SECONDS - .2, "the engine did not wait the abort's time for the open to come back after all"
        assert not of(w, "s", "session_end") and not of(w, "s", "failure"), of(w, "s")
    finally:
        put_away(w)


def test_a_failure_after_an_abort_does_not_give_the_abort_its_time_again(tmp_path):
    w = worker(tmp_path, "abort-then-failure")
    try:
        will_not_retire(w, "s")
        began = time.monotonic()
        w.call("close", session_id="s", mode="abort")
        time.sleep(ABORT_SECONDS * .6)
        w.input.close()
        code = w.p.wait(timeout=ABORT_SECONDS + 2)
        after = time.monotonic() - began
        assert code == ABANDONED, code
        assert ABORT_SECONDS - .1 <= after < ABORT_SECONDS + .6, f"{after:.2f} s: the failure moved the abort's deadline"
    finally:
        put_away(w)


def test_an_opening_that_waits_for_its_open_says_so(tmp_path, monkeypatch):
    w, gate = held_open(tmp_path, monkeypatch, "slow-open", opening_notice_ms=300)
    try:
        began = time.monotonic()
        waits = lambda: [json.loads(line.removeprefix("AII_VOICE_SETTINGS ")) for line in (w.out / "stderr.log").read_text().splitlines()
                         if line.startswith("AII_VOICE_SETTINGS ") and '"opening_waits"' in line]
        while not waits():
            assert time.monotonic() - began < 3, "an open that took its time was never said to be waiting"
            time.sleep(.01)
        assert [(l["session_id"], l["waiting_for"]) for l in waits()] == [("s", "open")], waits()
        assert w.status("s")["opening"]["waiting_for"] == "open"
        (gate / "release").write_text("1")
        w.event("session_ready", "s", timeout=3)
        assert "opening" not in w.status("s")
        w.call("close", session_id="s", mode="abort")
        w.event("session_end", "s")
    finally:
        assert w.close() == 0
