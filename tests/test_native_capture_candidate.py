import numpy as np
import pytest

from experiments.native_capture_candidate import ROOT, transform
from runtime.voice_core.native_stream import NativePCM
from scripts.probe_native_capture_candidate import distribution
from tests.test_native_stream import ready, row

BASE_SOURCE = (
    ROOT
    / "experiments/results/vf-078/source"
    / "eecbae2f26941cd30be7facc6073c18424f86515f5a6161315ebb95809da3ae5"
    / "runtime/native_audio/macos/Sources/AIIAudioHost/main.swift"
)


@pytest.mark.parametrize("variant", ["sink", "sink-notify"])
def test_candidate_preserves_original_and_control_seams(variant):
    # Historical experiments remain reproducible after a later host promotion.
    # The builder still binds the live source and refuses baseline drift.
    original = BASE_SOURCE.read_text()
    result = transform(original, variant)
    assert BASE_SOURCE.read_text() == original
    assert "input.installTap" not in result
    assert "AVAudioSinkNode {" in result
    assert "input.setVoiceProcessingEnabled(processing)" in result
    assert "input.isVoiceProcessingBypassed = false" in result
    assert 'case "cancel":' in result
    assert "AudioHardwareDestroyAggregateDevice(aggregateDevice)" in result
    assert '"capture_delivery": "' + variant + '"' in result
    assert ("AudioUnitAddRenderNotify(referenceUnit" in result) == (
        variant == "sink-notify"
    )
    assert ("engine.mainMixerNode.installTap" in result) == (variant == "sink")


def test_candidate_refuses_source_drift_and_unknown_arm():
    source = BASE_SOURCE.read_text()
    with pytest.raises(ValueError, match="binding changed"):
        transform(source + "\n", "sink")
    with pytest.raises(ValueError, match="unknown"):
        transform(source, "tap")


def test_callback_does_not_add_work_to_audio_thread():
    source = transform(BASE_SOURCE.read_text(), "sink-notify")
    callbacks = source[
        source.index("func copyNativeAudio") : source.index("final class Host")
    ]
    for forbidden in (
        "DispatchQueue",
        "FileHandle",
        "JSONSerialization",
        "NSLock",
        "Array(",
    ):
        assert forbidden not in callbacks
    assert "vf_queue_push" in callbacks
    assert "unitRenderAction_PostRenderError" in callbacks
    assert "first.mDataByteSize >= count * 4" in callbacks


def test_new_native_cadences_preserve_every_model_and_control_sample():
    # Includes a non-frame-sized ending and a strong last sample: no tail crop.
    rng = np.random.default_rng(78)
    microphone = rng.normal(0, 0.02, 19373).astype(np.float32)
    microphone[-1] = 0.75
    reference = rng.normal(0, 0.01, 19373).astype(np.float32)

    def replay(mic_chunk, render_chunk):
        pending = []
        for name, data, chunk in (
            ("microphone", microphone, mic_chunk),
            ("render", reference, render_chunk),
        ):
            for start in range(0, len(data), chunk):
                end = min(start + chunk, len(data))
                pending.append((end, name, row(name, data[start:end], start)))
        pcm, pairs, fast = NativePCM(ready()), [], []
        for _, _, packet in sorted(pending):
            pairs.extend(pcm.push(packet))
            fast.extend(pcm.fast_blocks())
        tail, metadata = pcm.finish()
        pairs.extend(tail)
        fast.extend(pcm.fast_blocks())
        return (
            np.concatenate([p[0] for p in pairs]),
            np.concatenate([p[1] for p in pairs]),
            np.concatenate(fast),
            metadata,
        )

    baseline = replay(4800, 4800)
    for sizes in ((480, 4800), (480, 480), (128, 256), (960, 480), (512, 4800)):
        candidate = replay(*sizes)
        for expected, actual in zip(baseline[:3], candidate[:3]):
            np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(candidate[0], candidate[2])
        assert candidate[3] == baseline[3]
    assert baseline[3]["native_microphone_samples"] == len(microphone)
    assert baseline[3]["fir_tail_48k_samples"] == 126


def test_latency_estimator_refuses_missing_evidence_and_keeps_reference_delay():
    for values in ([], [float("nan")], [float("inf")]):
        with pytest.raises(ValueError, match="timing evidence"):
            distribution(values)
    assert distribution([100, 100, 100])["p95_ms"] == 100
    assert distribution([10, 10, 10])["p95_ms"] == 10
    # Hardware render times may describe future presentation: preserve them,
    # rather than clipping into a bogus all-zero latency result.
    assert distribution([-10, -10])["p50_ms"] == -10
