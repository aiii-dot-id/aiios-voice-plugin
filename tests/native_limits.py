"""The worker's time limits as its carrier hands them over.

One table (runtime/native/session/worker_limits.h), strict: every member, integers in
milliseconds, no white space. TABLE is the one the carrier computes from its defaults
(plugin/native/limits.go, forWorker), and the one the stand-in hands a worker where a test
states none; tests/test_runtime_limits.py reads the carrier's source and fails when they
differ. A test that is about a member states it, limits(member=...), and hands the table to
the stand-in: Worker(..., limits=limits(...)).
"""
import json

TABLE = {
    "exchange_read_ms": 5500, "exchange_write_ms": 12500, "opening_ms": 12500, "whole_read_ms": 12500,
    "whole_publication_ms": 50000, "drain_idle_ms": 15000, "reply_settings_ms": 150,
    "abort_ms": 5000, "capture_close_ms": 45000, "session_open_ms": 60000, "opening_notice_ms": 1500,
    "audio_write_ms": 3000, "control_write_ms": 3000, "retire_ms": 5000, "capture_tail_ms": 2000,
    "model_call_ms": 30000, "input_tail_ms": 3000, "output_take_ms": 15000, "speaker_match_ms": 15000,
    "warm_probe_ms": 40000, "endpoint_decision_ms": 1000, "endpoint_retire_ms": 30500,
    "separation_min_ms": 4000, "separation_max_ms": 25000,
}


def limits(**changed):
    unknown = set(changed) - set(TABLE)
    if unknown:
        raise KeyError(f"not a member of the worker's limits: {sorted(unknown)}")
    return json.dumps({**TABLE, **changed}, separators=(",", ":"))
