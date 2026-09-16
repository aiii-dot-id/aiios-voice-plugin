import copy

import pytest

from scripts.audit_native_pocket_resident import validate_execution


def valid():
    complete = {"state": "completed", "produced_samples": 42240, "delivered_samples": 42240,
                "queued_samples": 0, "peak_queued_samples": 1920,
                "playback_state": "host_owned_not_observed"}
    return {"passed": True, "no_torch_import": True,
            "environment": {"GGML_VK_DISABLE_F16": "1", "GGML_VK_VISIBLE_DEVICES": "0"},
            "interruption": {"inference_observed_active": True, "pending_after_ack": True,
                             "late_pcm_refused": True, "ack_ms": .04, "retired_ms": 128.0},
            "frame_limit": {"failure": "frame limit", "state": 8, "emitted_samples": 3840},
            "shared_speech_output": {"complete": complete, "recovery": copy.deepcopy(complete),
                                     "cancel_ack_ms": .05,
                                     "cancelled": {"state": "cancelled", "produced_samples": 0, "delivered_samples": 0}}}


def test_resident_execution_is_a_component_not_playback_proof():
    validate_execution(valid())


@pytest.mark.parametrize(("path", "value"), [
    (("passed",), False), (("no_torch_import",), False),
    (("environment", "GGML_VK_VISIBLE_DEVICES"), "cpu"),
    (("interruption", "inference_observed_active"), False),
    (("interruption", "pending_after_ack"), False),
    (("interruption", "late_pcm_refused"), False),
    (("interruption", "ack_ms"), 51), (("interruption", "ack_ms"), float('nan')),
    (("interruption", "retired_ms"), 10001),
    (("frame_limit", "failure"), None), (("frame_limit", "state"), 0),
    (("frame_limit", "emitted_samples"), 1920),
    (("shared_speech_output", "cancel_ack_ms"), 51),
    (("shared_speech_output", "complete", "delivered_samples"), 42239),
    (("shared_speech_output", "recovery", "produced_samples"), 42239),
    (("shared_speech_output", "recovery", "queued_samples"), 1920),
    (("shared_speech_output", "complete", "peak_queued_samples"), 3840),
    (("shared_speech_output", "complete", "playback_state"), "drained"),
    (("shared_speech_output", "cancelled", "delivered_samples"), 1),
    (("shared_speech_output", "cancelled", "state"), "completed"),
])
def test_false_resident_execution_claims_are_refused(path, value):
    result = valid()
    parent = result
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    with pytest.raises(ValueError):
        validate_execution(result)
