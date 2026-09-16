"""Resident-lane UID evidence and the host's delivered observation shape.

This is attribution evidence, never authentication or a second transcript.
The host owns SAFE, settings and authorized enrollment. Until that joint seam is
installed, the shipping worker does not enable this optional adapter by itself.
"""

import math

from runtime.speaker_identity.backend import MODEL_SHA256


def host_observation(result, policy):
    """Adapt validated engine evidence to the landed host attribution contract.

    Host a5d203a0 reads a string speaker and refers_to/decision/score/late.
    The engine's richer evidence remains nested; it cannot replace those
    routing fields. Missing measurements stay missing, never synthetic zero.
    All these observations follow their final transcript, hence late=True.
    No permissions or turn role are inferred from a known-speaker decision.
    """
    evidence = observation(result, policy)
    outcome = evidence["outcome"]
    body = {
        "refers_to": evidence["attributes"],
        "decision": {
            "known": "known",
            "unknown": "unknown",
            "ambiguous": "uncertain",
            "unavailable": "uncertain",
        }[outcome],
        "speaker": evidence["speaker"]["label"] if outcome == "known" else "",
        "late": True,
        "evidence": evidence,
    }
    if "similarity" in evidence:
        body["score"] = evidence["similarity"]["value"]
    if "cause" in evidence:
        body["reason"] = evidence["cause"]
    return body


def observation(result, policy):
    """Project the existing result without inventing confidence or model facts.

    The model revision is the embedding-space binding (weights plus frontend).
    Absent measurements stay absent; unavailable audio cannot acquire a hash.
    Policy revision and margin are needed to reproduce a thresholded decision,
    and are part of the candidate sent to the host, not a frozen SDK extension.
    """
    sequence = result.get("attributes")
    if type(sequence) is not int or sequence < 1:
        raise ValueError("speaker observation requires its final's sequence")
    outcome = result.get("outcome")
    if outcome not in {"known", "unknown", "ambiguous", "unavailable"}:
        raise ValueError("unsupported speaker observation outcome")
    if outcome != "unavailable" and (
        result.get("embedding_binding") != policy.embedding_binding
        or result.get("policy_sha256") != policy.fingerprint
    ):
        raise ValueError("speaker observation model/policy differs")
    body = {
        "attributes": sequence,
        "outcome": outcome,
        "model": {
            "id": "wespeaker-voxblink2-samresnet34",
            "revision": policy.embedding_binding,
            "weights_sha256": MODEL_SHA256,
        },
        "policy_revision": policy.fingerprint,
    }
    if outcome == "unavailable":
        reason = result.get("reason")
        if not isinstance(reason, str) or not reason:
            raise ValueError("unavailable speaker observation requires a cause")
        body["cause"] = reason
    else:
        revision = result.get("enrollment_revision")
        if type(revision) is not int or revision < 0:
            raise ValueError("speaker observation requires enrollment revision")
        body["enrollment_revision"] = str(revision)
    if outcome == "known":
        sid, label = result.get("speaker_id"), result.get("label")
        if (
            not isinstance(sid, str)
            or not sid
            or not isinstance(label, str)
            or not label
        ):
            raise ValueError("known observation requires speaker id and label")
        body["speaker"] = {"id": sid, "label": label}
    elif result.get("speaker_id") is not None or result.get("label") is not None:
        raise ValueError("a refused observation cannot name a speaker")
    start, end = result.get("start_sample"), result.get("end_sample")
    if type(start) is int and type(end) is int and 0 <= start < end:
        if result.get("sample_rate") != 16000:
            raise ValueError("speaker observation requires engine-clock samples")
        body["audio"] = {"start_sample": start, "end_sample": end, "sample_rate": 16000}
        pcm_hash = result.get("audio_sha256")
        if pcm_hash is not None:
            if (
                not isinstance(pcm_hash, str)
                or len(pcm_hash) != 64
                or any(c not in "0123456789abcdef" for c in pcm_hash)
            ):
                raise ValueError("speaker observation has invalid PCM hash")
            body["audio"]["pcm_sha256"] = pcm_hash
        elif outcome != "unavailable":
            raise ValueError("completed identification requires exact PCM hash")
    elif outcome != "unavailable":
        raise ValueError("completed identification requires an exact sample span")
    score, margin = result.get("score"), result.get("margin")
    if score is not None:
        if (
            outcome == "unavailable"
            or type(score) not in (int, float)
            or not math.isfinite(score)
            or not -1 <= score <= 1
        ):
            raise ValueError("speaker cosine must be finite and on its original scale")
        body["similarity"] = {
            "measure": "cosine",
            "value": score,
            "threshold": policy.threshold,
            "minimum_margin": policy.minimum_margin,
        }
        if margin is not None:
            if (
                type(margin) not in (int, float)
                or not math.isfinite(margin)
                or not 0 <= margin <= 2
            ):
                raise ValueError(
                    "speaker margin must be finite and on its original scale"
                )
            body["similarity"]["margin"] = margin
    elif outcome in {"known", "ambiguous"} or margin is not None:
        raise ValueError("speaker outcome requires a measured cosine")
    return body
