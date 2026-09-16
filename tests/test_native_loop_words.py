import pytest

from scripts.audit_native_loop import interruption_words


def fixture():
    return (
        [{"synthesis_id": "s1", "observed_monotonic_ns": 102}],
        [{"synthesis_id": "s1"}],
        [{"start_sample": 512, "probability": 0.9, "observed_monotonic_ns": 100}],
        [
            {
                "start_sample": 0,
                "end_sample": 2048,
                "observed_monotonic_ns": 200,
                "text": "Please stop now. Cobalt lantern seventeen.",
            }
        ],
    )


def test_words_belong_to_the_utterance_that_interrupted():
    assert interruption_words("Please stop now cobalt lantern seventeen", *fixture())
    requests, genuine, vad, final = fixture()
    final[0]["start_sample"] = 1024
    with pytest.raises(ValueError, match="interrupting STT turn"):
        interruption_words("cobalt lantern seventeen", requests, genuine, vad, final)


def test_partial_words_and_empty_expectation_cannot_pass():
    for text in ("cobalt lantern seven", "cobalt lantern seventee"):
        with pytest.raises(ValueError, match="interrupting STT turn"):
            interruption_words(text, *fixture())
    with pytest.raises(ValueError, match="empty expected"):
        interruption_words("", *fixture())


def test_manual_or_non_speech_cancellation_is_not_the_gate():
    requests, genuine, vad, final = fixture()
    with pytest.raises(ValueError, match="automatic speech evidence"):
        interruption_words("cobalt lantern seventeen", requests, [], vad, final)
    vad[0]["probability"] = 0.1
    with pytest.raises(ValueError, match="lacks its speech block"):
        interruption_words("cobalt lantern seventeen", requests, genuine, vad, final)


def test_predeclared_phrase_may_be_in_a_later_genuine_interruption():
    requests, genuine, vad, final = fixture()
    final[0]["text"] = "Hang on just a second."
    requests.append({"synthesis_id": "s2", "observed_monotonic_ns": 302})
    genuine.append({"synthesis_id": "s2"})
    vad.append({"start_sample": 4096, "probability": 0.9, "observed_monotonic_ns": 300})
    final.append(
        {
            "start_sample": 3584,
            "end_sample": 6144,
            "observed_monotonic_ns": 400,
            "text": "Please stop now keep the words cobalt lantern seventeen.",
        }
    )
    expected = "Please stop now keep the words cobalt lantern seventeen"
    assert (
        interruption_words(expected, requests, genuine, vad, final) == final[1]["text"]
    )
    # The same words in a later, unrelated utterance still do not pass.
    final[1]["start_sample"] = 4608
    with pytest.raises(ValueError, match="interrupting STT turn"):
        interruption_words(expected, requests, genuine, vad, final)
