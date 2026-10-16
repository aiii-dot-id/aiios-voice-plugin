"""docs/NATIVE_WORKER_WIRE.md is held to the source it describes.

The document is prose, and prose falls behind. This test keeps it honest
cheaply, by names. It reads the carrier's source (plugin/native/*.go, tests
left out) and the worker's (runtime/native/session), takes every name that is
on the private wire by the rules below, and fails when one of them is not in
the document inside code formatting (a code span or a fenced block), as a
whole word. A new operation, event, member, reason code, diagnostic or limit
therefore cannot be added to the code without the document being opened.

It does not read the prose. A name that is in the document beside a wrong
sentence passes.

THE EXTRACTION RULES

operations     every string literal of the form speech.session.x, speaker.x,
               recording.x or vocabulary.x in worker.cpp and in the carrier
event types    every string literal in the first argument of an emit( call in
               worker.cpp; and every string literal in the sixth member, the
               kind, of an Event{...} built in session.cpp
line members   every json tag of the carrier's wire structs: workerMessage,
               privateRequest, settingsQuery, settingsRequest, settingsReply,
               snapshotQuery, snapshotReply; every key the worker puts on a
               line it sends, put(message|query|ready|identity|details, "k"
               in worker.cpp, and reads from a line it is sent,
               field(j.get(), "k") and field(p, "k") in worker.cpp; every key
               put or read anywhere in snapshot_bridge.cpp
limits         every json tag of the carrier's workerLimits and profileLimits,
               and every member name in worker_limits.h's parse table
reason codes   every string literal HOST_... or FS_... in the carrier, in
               worker.cpp and in snapshot_bridge.cpp and .h
diagnostics    every AII_... name that begins a string literal in the worker's
               wire files, and every AII_... name anywhere in the carrier;
               every key put(diagnostic|held|gap|foreign, "k" in worker.cpp;
               every diagnostic event name: "event", string("x"),
               say_speech("x" and report_held("x" in worker.cpp, and
               "event":"x" in the carrier
exit statuses  the number in every abandon(n) in worker.cpp, the two in
               worker_liveness.h, and the one the Windows carrier ends its
               job with
storage words  every resource and action the storage bridge writes, and the
               carrier's own correction-list resource

Each rule must find at least a known few names, so that a rule that has
stopped matching the source fails here and is not passed as "nothing missing".

THE TABLE OF DIFFERENCES

The first table of the document's last section says, for each kind of line,
which members its writer writes that its reader never reads, and which its
reader reads that its writer never writes. This test computes both sets from
the same rules and requires the table to say exactly that. When one side is
changed to match the other, the table has to be changed too.

The carrier's, the worker's and the tests' copies of the limits table are also
held to the same member names.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "docs/NATIVE_WORKER_WIRE.md"
SESSION = ROOT / "runtime/native/session"


def text(path):
    return path.read_text(encoding="utf-8")


CARRIER = "\n".join(text(p) for p in sorted((ROOT / "plugin/native").glob("*.go")) if not p.name.endswith("_test.go"))
WORKER = text(SESSION / "worker.cpp")
BRIDGE = text(SESSION / "snapshot_bridge.cpp")
BRIDGE_HEADER = text(SESSION / "snapshot_bridge.h")
LIMITS_HEADER = text(SESSION / "worker_limits.h")
LIVENESS = text(SESSION / "worker_liveness.h")
CORE = text(SESSION / "session.cpp")
WORKER_WIRE_FILES = "\n".join(
    [WORKER, BRIDGE, BRIDGE_HEADER, LIMITS_HEADER, LIVENESS, text(SESSION / "worker_io.cpp"), text(SESSION / "installed_profile.h")])


def go_struct_tags(name):
    """The json tags of one struct the carrier declares at the top level."""
    block = re.search(r"^type %s struct \{\n(.*?)^\}" % re.escape(name), CARRIER, re.S | re.M)
    assert block, "the carrier no longer declares the struct " + name
    return set(re.findall(r'json:"([a-z0-9_]+)', block.group(1)))


def go_function_tags(name):
    """The json tags inside one top-level function of the carrier."""
    body = re.search(r"^func %s\(.*?^\}" % re.escape(name), CARRIER, re.S | re.M)
    assert body, "the carrier no longer declares the function " + name
    return set(re.findall(r'json:"([a-z0-9_]+)', body.group(0)))


def put_keys(source, *variables):
    """Every key put on one of these C++ variables: put(variable, "key", ..."""
    return set(re.findall(r'\bput\(\s*(?:%s)\s*,\s*"([a-z0-9_]+)"' % "|".join(variables), source))


def field_keys(source, *expressions):
    """Every key read from one of these C++ expressions: field(expression, "key")."""
    return set(re.findall(r'\bfield\(\s*(?:%s)\s*,\s*"([a-z0-9_]+)"\s*\)' % "|".join(map(re.escape, expressions)), source))


OPERATION = r'"((?:speech\.session|speaker|recording|vocabulary)\.[a-z_]+)"'


def worker_event_types():
    names = set()
    for first_argument in re.findall(r"\bemit\(([^,;]*)", WORKER):
        names |= set(re.findall(r'"([a-z_]+)"', first_argument))
    return names


def core_event_kinds():
    names = set()
    for kind in re.findall(r"Event(?:\s+\w+)?\{(?:[^,;{}]*,){5}\s*([^,;]*)", CORE):
        names |= set(re.findall(r'"([a-z_]+)"', kind))
    return names


def worker_limit_members():
    return set(re.findall(r'\{"([a-z_]+_ms)",\s*&limits\.', LIMITS_HEADER))


WIRE_STRUCTS = ("workerMessage", "privateRequest", "settingsQuery", "settingsRequest", "settingsReply", "snapshotQuery", "snapshotReply")

# What each rule finds, and a few names it must find for the rule to count as
# still reading the source.
NAMES = {
    "operations": (
        set(re.findall(OPERATION, WORKER)) | set(re.findall(OPERATION, CARRIER)),
        {"speech.session.open", "speech.session.playback_report", "speaker.link", "recording.record", "recording.prune", "vocabulary.correct"}),
    "event types": (
        worker_event_types() | core_event_kinds(),
        {"session_start", "session_end", "failure", "synthesis_end", "synthesis_cancelled", "transcript_final", "pause_resolved", "input_finished"}),
    "line members": (
        set().union(*[go_struct_tags(name) for name in WIRE_STRUCTS])
        | put_keys(WORKER, "message", "query", "ready", "identity", "details")
        | field_keys(WORKER, "j.get()", "p")
        | set(re.findall(r'\bput\(\s*[a-z_]+\s*,\s*"([a-z0-9_]+)"', BRIDGE))
        | set(re.findall(r'\bfield\([^"]*?,\s*"([a-z0-9_]+)"\s*\)', BRIDGE)),
        {"id", "operation", "arguments", "result", "error", "event", "ready", "settings_request", "settings_reply", "snapshot_request",
         "snapshot_reply", "refresh", "values", "corrections", "reason_code", "value", "data_b64", "expected_absent",
         "durability", "accelerator_scope", "probe_ms"}),
    "limits": (
        go_struct_tags("workerLimits") | go_struct_tags("profileLimits") | worker_limit_members(),
        {"exchange_read_ms", "whole_publication_ms", "reply_settings_ms", "host_write_ms", "storage_wait_ms"}),
    "reason codes": (
        set(re.findall(r'"((?:HOST|FS)_[A-Z_]+)"', CARRIER + WORKER + BRIDGE + BRIDGE_HEADER)),
        {"HOST_SETTINGS_NO_ANSWER", "HOST_SETTINGS_ERROR", "HOST_SETTINGS_NOT_SETTINGS", "HOST_STORAGE_NO_ANSWER", "FS_NOT_FOUND"}),
    "diagnostic and environment names": (
        set(re.findall(r'"(AII_[A-Z0-9_]+)', WORKER_WIRE_FILES)) | set(re.findall(r"\b(AII_[A-Z0-9_]+)", CARRIER)),
        {"AII_VOICE_FAILURE", "AII_VOICE_BACKPRESSURE", "AII_VOICE_GAP", "AII_VOICE_FOREIGN_INPUT", "AII_VOICE_CORRECTIONS",
         "AII_VOICE_SETTINGS", "AII_VOICE_READY", "AII_VOICE_LIMITS", "AII_VOICE_CARRIER_LIVENESS_FD", "AII_AUDIO_IN_FD",
         "AII_AUDIO_OUT_FD"}),
    "diagnostic members": (
        put_keys(WORKER, "diagnostic", "held", "gap", "foreign"),
        {"component", "event", "session_id", "reason", "settings", "held_ms", "queued_frames", "samples", "declared"}),
    "diagnostic events": (
        set(re.findall(r'"event"\s*,\s*string\("([a-z_]+)"\)', WORKER))
        | set(re.findall(r'\b(?:say_speech|report_held)\("([a-z_]+)"', WORKER))
        | set(re.findall(r'"event":"([a-z_]+)"', CARRIER)),
        {"failure", "session_settings", "speech_settings", "speech_settings_not_taken", "input_backpressure", "input_gap",
         "foreign_input", "corrections_unreadable", "corrections_unavailable"}),
    "exit statuses": (
        set(re.findall(r"\babandon\((\d+)\)", WORKER))
        | {n for pair in re.findall(r"_Exit\(count == 0 \? (\d+) : (\d+)\)", LIVENESS) for n in pair}
        | set(re.findall(r"workerTreeJob\),\s*(\d+)\)", CARRIER)),
        {"72", "73", "74", "75"}),
    "storage words": (
        set(re.findall(r'"resource"\s*,\s*string\("([a-z_:]+)"', BRIDGE))
        | set(re.findall(r'"action"\s*,\s*string\("([a-z_]+)"\)', BRIDGE))
        | set(re.findall(r'correctionsResource\s*=\s*"([a-z_]+)"', CARRIER)),
        {"captures", "speaker_registry", "recovery:", "waveform:", "stage", "publish", "corrections"}),
}


def document_code(document):
    """Everything the document sets as code: its fenced blocks and its code spans."""
    fenced = re.findall(r"```.*?```", document, re.S)
    rest = re.sub(r"```.*?```", "", document, flags=re.S)
    return "\n".join(fenced + re.findall(r"`([^`\n]+)`", rest))


def documented(name, code):
    return re.search(r"(?<![A-Za-z0-9_.])" + re.escape(name) + r"(?![A-Za-z0-9_])", code) is not None


@pytest.mark.parametrize("rule", sorted(NAMES))
def test_every_wire_name_in_the_source_is_in_the_document(rule):
    found, known = NAMES[rule]
    lost = sorted(known - found)
    assert not lost, f"the rule for {rule} no longer finds {lost} in the source; the rule or the source moved"
    code = document_code(text(DOCUMENT))
    absent = sorted(name for name in found if not documented(name, code))
    assert not absent, f"docs/NATIVE_WORKER_WIRE.md does not name these {rule} that the source holds: {absent}"


def computed_differences():
    """For each kind of line: (written and never read, read and never written)."""
    query = go_struct_tags("settingsQuery")
    lines = {
        "request line": (go_struct_tags("privateRequest"), field_keys(WORKER, "j.get()")),
        "settings_reply": (query | go_struct_tags("settingsReply"), field_keys(WORKER, "p")),
        "snapshot_reply": (query | go_struct_tags("snapshotReply"),
                           field_keys(BRIDGE, "j", "reply", "result.get()", "receipt.get()", "check.get()")),
        "worker line": (put_keys(WORKER, "message") | put_keys(BRIDGE, "message"), go_struct_tags("workerMessage")),
        "ready": (put_keys(WORKER, "ready", "identity", "details"), go_function_tags("readinessReport")),
        "settings_request": (put_keys(WORKER, "query"), query | go_struct_tags("settingsRequest")),
        "snapshot_request": (put_keys(BRIDGE, "q", "query"), query | go_struct_tags("snapshotQuery")),
        "AII_VOICE_LIMITS": (go_struct_tags("workerLimits"), worker_limit_members()),
    }
    for name, (written, read) in lines.items():
        assert written and read, f"nothing was found on one side of the {name}; a rule or the source moved"
    return {name: (written - read, read - written) for name, (written, read) in lines.items()}


def stated_differences(document):
    rows = document.splitlines()
    heading = "| Line | Written and never read | Read and never written |"
    assert rows.count(heading) == 1, "the document's table of differences is missing or repeated"
    stated = {}
    for row in rows[rows.index(heading) + 2:]:
        if not row.startswith("|"):
            break
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        assert len(cells) == 3, row
        sets = []
        for cell in cells[1:]:
            names = set(re.findall(r"`([^`]+)`", cell))
            rest = re.sub(r"`[^`]+`|[, ]", "", cell)
            assert (rest == "none" and not names) or (rest == "" and names), \
                f"a cell of the table of differences is neither 'none' nor a list of names: {row}"
            sets.append(names)
        stated[cells[0].strip("`")] = tuple(sets)
    return stated


def shown(differences):
    return {name: tuple(sorted(side) for side in sides) for name, sides in sorted(differences.items())}


def test_the_table_of_differences_says_what_the_source_does():
    computed, stated = computed_differences(), stated_differences(text(DOCUMENT))
    assert stated == computed, (
        "the first table of 'Where the three implementations differ' is not what the source does.\n"
        f"the document: {shown(stated)}\nthe source:   {shown(computed)}")


def test_the_three_copies_of_the_limits_table_name_the_same_members():
    carrier, worker = go_struct_tags("workerLimits"), worker_limit_members()
    tests = set(re.findall(r'"([a-z_]+_ms)":\s*\d+', text(ROOT / "tests/native_limits.py")))
    assert carrier == worker == tests and len(carrier) >= 10, (
        f"the carrier hands over {sorted(carrier)}, the worker parses {sorted(worker)}, the tests state {sorted(tests)}")


def test_the_document_keeps_its_sections():
    document = text(DOCUMENT)
    for heading in ("## 1. Process start", "## 2. The control channel, carrier to worker", "## 3. The control channel, worker to carrier",
                    "## 4. Ordering and matching", "## 5. The audio pipes", "## 6. Diagnostic lines",
                    "## 7. Where the three implementations differ"):
        assert document.count("\n" + heading + "\n") == 1, heading
