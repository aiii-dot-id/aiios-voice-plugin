"""A session's correction list rewrites what the recognizer wrote, and says so.

The list travels beside the session's settings and is pinned with them. A
transcript a rule changed carries the corrected words as its text, the
recognizer's own words as recognized_text, and how many corrections were made.
A session given no list, or one the engine cannot hold, is spoken to exactly as
before: nothing corrected, nothing added to its events.

Production worker, deterministic models: the fixture recognizer writes the
partial "opening words" and the final "opening words retained".
"""
import os
import struct
from pathlib import Path

from scripts.prove_native_worker_transport import Worker

PCM, END = 1, 3


def frame(kind, stream, seq, start, payload=b""):
    return struct.pack(">4sB3xIIQI", b"AUD1", kind, stream, seq, start, len(payload)) + payload


def speak_and_finish(w, sid, stream):
    """One utterance through to the session's end; returns its transcript events."""
    seq = start = 0
    for _ in range(4):
        seq += 1
        w.input.write(frame(PCM, stream, seq, start, b"\x00\x20" * 1024))
        start += 1024
    w.call("finish_input", session_id=sid, stream_id="capture", end_sample=start)
    w.input.write(frame(END, stream, seq + 1, start))
    w.event("input_finished", sid, timeout=10)
    w.call("close", session_id=sid, mode="drain")
    w.event("session_end", sid, timeout=10)
    mine = [e for e in w.events if e["session_id"] == sid]
    assert "failure" not in [e["type"] for e in mine], mine
    return [e for e in mine if e["type"] in ("transcript_partial", "transcript_final")]


def open_with(w, sid, corrections=None):
    q = w.open(sid, settings=False)
    reply = {**q, "values": {"turn_pause_ms": 768}}
    if corrections is not None:
        reply["corrections"] = corrections
    w.send({"settings_reply": reply})
    return w.event("session_ready", sid)


def document(*rules, revision=3):
    return {"schema": "aiii.voice.corrections", "revision": revision, "rules": [{"heard": h, "meant": m} for h, m in rules]}


def test_a_taught_list_corrects_partials_and_finals_and_carries_what_was_recognized(tmp_path):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "taught")
    try:
        ready = open_with(w, "taught", document(("Opening", "Quinn"), ("words retained", "was heard")))
        # "Quinn" is a term the fixture recognizer can prefer; "was heard" is two words to it.
        assert ready["models"]["corrections"] == {"revision": 3, "rules": 2, "preferred": 1}
        assert w.status("taught")["corrections"] == {"revision": 3, "rules": 2, "preferred": 1}
        transcripts = speak_and_finish(w, "taught", 1)
        partials = [e for e in transcripts if e["type"] == "transcript_partial"]
        finals = [e for e in transcripts if e["type"] == "transcript_final"]
        assert partials and len(finals) == 1, transcripts
        whole = ("Quinn was heard", "opening words retained", 2)
        for e in partials:  # the fixture's partials grow toward the final's words
            assert (e["text"], e["recognized_text"], e["corrections"]) in (("Quinn words", "opening words", 1), whole), e
        final = finals[0]
        assert (final["text"], final["recognized_text"], final["corrections"]) == whole, final
        assert final["attribution"]["used_for_permissions"] is False  # the rest of the final is as it was

        # The next session is given no list: the first one's does not carry over.
        ready = open_with(w, "untaught")
        assert "corrections" not in ready["models"] and "corrections" not in w.status("untaught")
        for e in speak_and_finish(w, "untaught", 2):
            assert e["text"] in ("opening words", "opening words retained") and "recognized_text" not in e and "corrections" not in e, e
    finally:
        assert w.close() == 0


def test_a_list_that_changes_nothing_adds_nothing(tmp_path):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "idle")
    try:
        # A rule for other words, and one whose word only appears inside a longer word.
        ready = open_with(w, "idle", document(("Kwin", "Quinn"), ("word", "term"), revision=1))
        assert ready["models"]["corrections"] == {"revision": 1, "rules": 2, "preferred": 2}
        for e in speak_and_finish(w, "idle", 1):
            assert "recognized_text" not in e and "corrections" not in e, e
    finally:
        assert w.close() == 0


def test_what_a_rule_meant_is_what_the_recognizer_prefers_for_that_session_only(tmp_path):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "preferred")
    try:
        # The rule's heard words never occur, so nothing is corrected: what
        # changes the transcript is the recognizer preferring what was meant.
        ready = open_with(w, "named", document(("zzz", "Opening"), ("yyy", "Opening"), ("xxx", "two words"), revision=5))
        assert ready["models"]["corrections"] == {"revision": 5, "rules": 3, "preferred": 2}
        for e in speak_and_finish(w, "named", 1):
            assert e["text"] in ("Opening words", "Opening words retained") and "recognized_text" not in e, e
        # A session with no list is given no terms: the first one's are gone.
        open_with(w, "plain")
        for e in speak_and_finish(w, "plain", 2):
            assert e["text"] in ("opening words", "opening words retained"), e
        # And a list the engine cannot hold prefers nothing.
        ready = open_with(w, "broken", {"schema": "other", "revision": 1, "rules": []})
        assert set(ready["models"]["corrections"]) == {"unreadable"}
        for e in speak_and_finish(w, "broken", 3):
            assert e["text"] in ("opening words", "opening words retained"), e
    finally:
        assert w.close() == 0


def test_a_list_the_engine_cannot_hold_leaves_the_session_speaking_uncorrected(tmp_path):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "unreadable")
    try:
        cases = {
            "wrong-schema": {"schema": "other", "revision": 1, "rules": []},
            "bad-rule": document(("opening, words", "x")),
            "twice": document(("opening", "a"), ("OPENING", "b")),
            "not-an-object": ["opening", "x"],
        }
        for stream, (name, stored) in enumerate(cases.items(), 1):
            ready = open_with(w, name, stored)
            state = ready["models"]["corrections"]
            assert set(state) == {"unreadable"} and state["unreadable"], state
            for e in speak_and_finish(w, name, stream):
                assert "recognized_text" not in e and "opening" in e["text"], e
        log = (tmp_path / "unreadable" / "stderr.log").read_text()
        assert log.count("AII_VOICE_CORRECTIONS ") == len(cases) and '"event":"corrections_unreadable"' in log
        assert "opening" not in log  # the diagnostic names the fault, never a rule's words
    finally:
        assert w.close() == 0


def test_a_meant_longer_than_a_term_costs_no_session_and_is_said(tmp_path):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "long")
    try:
        # Twenty-two CJK characters are a rule (a side is 64 characters at
        # most) and 66 bytes: more than the recognizer takes as a term, which
        # is 64 bytes. Such a meant is not offered to it. The session opens,
        # its readback says how many terms were kept and how many were too
        # long to offer, and the rule still rewrites what was heard.
        long_meant = (chr(0x6771) + chr(0x4EAC)) * 11
        assert len(long_meant) == 22 and len(long_meant.encode()) == 66
        # Two of the three are too long and one is offered, so the two counts
        # cannot be taken for each other.
        ready = open_with(w, "long", document(("Opening", long_meant), ("words", "terms"), ("zzz", long_meant), revision=7))
        kept = {"revision": 7, "rules": 3, "preferred": 1, "too_long_to_prefer": 2}
        assert ready["models"]["corrections"] == kept
        assert w.status("long")["corrections"] == kept
        finals = [e for e in speak_and_finish(w, "long", 1) if e["type"] == "transcript_final"]
        assert len(finals) == 1, finals
        final = finals[0]
        assert (final["text"], final["recognized_text"], final["corrections"]) == (long_meant + " terms retained", "opening words retained", 2), final

        # The bound is the recognizer's own: a meant of exactly 64 bytes is
        # offered and kept, one of 65 bytes is not, and neither fails the open.
        at_the_bound = chr(0xE9) * 32
        assert len(at_the_bound.encode()) == 64
        ready = open_with(w, "bound", document(("zzz", at_the_bound), ("yyy", at_the_bound + "a"), revision=8))
        assert ready["models"]["corrections"] == {"revision": 8, "rules": 2, "preferred": 1, "too_long_to_prefer": 1}
        for e in speak_and_finish(w, "bound", 2):
            assert e["text"] in ("opening words", "opening words retained") and "recognized_text" not in e, e
    finally:
        assert w.close() == 0


def test_what_was_meant_is_written_with_its_joiners_and_what_was_heard_takes_none(tmp_path):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "joined")
    try:
        non_joiner, joiner = chr(0x200C), chr(0x200D)
        # A Persian word is written with its non-joiner, and an emoji made by
        # joining three with the joiners between them. Both are what a rule
        # may write, both are offered to the recognizer as terms like any
        # other, and the transcript holds them exactly as they were taught.
        persian = "".join(map(chr, (0x645, 0x6CC))) + non_joiner + "".join(map(chr, (0x62E, 0x648, 0x627, 0x647, 0x645)))
        family = chr(0x1F468) + joiner + chr(0x1F469) + joiner + chr(0x1F467)
        ready = open_with(w, "joined", document(("Opening", persian), ("words", family), revision=9))
        assert ready["models"]["corrections"] == {"revision": 9, "rules": 2, "preferred": 2}
        finals = [e for e in speak_and_finish(w, "joined", 1) if e["type"] == "transcript_final"]
        assert len(finals) == 1, finals
        final = finals[0]
        assert (final["text"], final["recognized_text"], final["corrections"]) == (persian + " " + family + " retained", "opening words retained", 2), final

        # A joiner that joins nothing is not spelling, and what was heard takes
        # no joiner at all: such a list is one the engine cannot hold.
        cases = {
            "last": document(("opening", persian + non_joiner)),
            "by-a-space": document(("opening", "a " + joiner + "b")),
            "doubled": document(("opening", "a" + joiner + non_joiner + "b")),
            "heard": document(("open" + non_joiner + "ing", "x")),
        }
        for stream, (name, stored) in enumerate(cases.items(), 2):
            ready = open_with(w, name, stored)
            state = ready["models"]["corrections"]
            assert set(state) == {"unreadable"} and state["unreadable"], (name, state)
            for e in speak_and_finish(w, name, stream):
                assert "recognized_text" not in e and "opening" in e["text"], e
    finally:
        assert w.close() == 0
