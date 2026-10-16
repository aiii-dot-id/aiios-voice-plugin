"""Fail-closed scoring of admission and word preservation on labelled windows.

This is a gate, not an echo classifier. It cannot make a detector good by
discarding bad samples or pretending an unlabelled interval is silence.
"""

from __future__ import annotations

import re


def words(text):
    return re.findall(r"\w+", text.casefold())


def score(windows, admissions):
    """Score sample-aligned candidate admissions with their final transcripts.

    A point is the detector's decision sample, not a claimed true human onset.
    Every labelled human window needs exactly one admission and preservation
    of its baseline words. Echo-only and unknown admissions both refuse pass.
    """
    if not windows:
        raise ValueError("empty reference cannot qualify admission")
    previous_end = -1
    ids = set()
    for window in windows:
        if (
            window["kind"] not in {"human", "echo", "unknown"}
            or not isinstance(window["id"], str)
            or not window["id"]
            or window["id"] in ids
            or type(window["start"]) is not int
            or type(window["end"]) is not int
            or window["start"] < 0
            or window["start"] < previous_end
            or window["end"] <= window["start"]
            or (
                window["kind"] == "human"
                and (
                    not isinstance(window.get("baseline_text"), str)
                    or not words(window["baseline_text"])
                )
            )
        ):
            raise ValueError("invalid or overlapping annotation windows")
        ids.add(window["id"])
        previous_end = window["end"]
    hits = {window["id"]: [] for window in windows}
    echo, unknown, missing, repeated, damaged = [], [], [], [], []
    last_point = -1
    for admission in admissions:
        point = admission["decision_sample"]
        if (
            type(point) is not int
            or point <= last_point
            or not isinstance(admission["text"], str)
        ):
            raise ValueError("admissions must have ordered decision samples and text")
        last_point = point
        matches = [w for w in windows if w["start"] <= point < w["end"]]
        if not matches:
            unknown.append(point)
            continue
        window = matches[0]
        hits[window["id"]].append(admission)
        if window["kind"] == "echo":
            echo.append(point)
        elif window["kind"] == "unknown":
            unknown.append(point)
    for window in windows:
        if window["kind"] != "human":
            continue
        rows = hits[window["id"]]
        if not rows:
            missing.append(window["id"])
        elif len(rows) > 1:
            repeated.append(window["id"])
        elif words(rows[0]["text"]) != words(window["baseline_text"]):
            damaged.append(window["id"])
    return {
        "status": "failed"
        if any((echo, unknown, missing, repeated, damaged))
        else "passed",
        "false_echo_admissions": echo,
        "unlabelled_admissions": unknown,
        "missing_human_windows": missing,
        "repeated_human_windows": repeated,
        "changed_human_transcripts": damaged,
        "latency_qualified": False,
        "scope": "development admission/ASR-preservation regression; not acoustic-onset or human-level qualification",
    }


def from_trace(trace):
    finals = {
        e["utterance_id"]: e["text"]
        for e in trace["events"]
        if e["type"] == "transcript_final"
    }
    return [
        {
            "decision_sample": e["end_sample"] - 1,
            "text": finals.get("u" + e["activity_id"][1:], ""),
        }
        for e in trace["events"]
        if e["type"] == "speech_start"
    ]


def windows_from_trace(trace, labels):
    label_map = {}
    for field, kind in (
        ("human_bearing", "human"),
        ("echo_only", "echo"),
        ("unlabelled", "unknown"),
    ):
        for activity in labels[field]:
            if activity in label_map:
                raise ValueError("activity labelled twice")
            label_map[activity] = kind
    ends = {
        e["activity_id"]: e["end_sample"]
        for e in trace["events"]
        if e["type"] == "speech_end"
    }
    finals = {
        e["utterance_id"]: e["text"]
        for e in trace["events"]
        if e["type"] == "transcript_final"
    }
    result = []
    for event in trace["events"]:
        if event["type"] != "speech_start":
            continue
        activity = event["activity_id"]
        if activity not in label_map or activity not in ends:
            raise ValueError("every original activity needs an explicit label and end")
        result.append(
            {
                "id": activity,
                "kind": label_map.pop(activity),
                "start": event["start_sample"],
                "end": ends[activity],
                "baseline_text": finals.get("u" + activity[1:], ""),
            }
        )
    if label_map:
        raise ValueError("annotation names absent activity")
    return result
