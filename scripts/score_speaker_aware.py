"""Score whole-session anonymous speaker streams; never optimize per segment."""
import argparse
import itertools
import json
import math
from pathlib import Path
import re


def words(text):
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", text.lower().replace("’", "'"))


def distance(a, b):
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        row = [i]
        for j, y in enumerate(b, 1):
            row.append(min(previous[j] + 1, row[-1] + 1, previous[j-1] + (x != y)))
        previous = row
    return previous[-1]


def streams(rows):
    result = {}
    for r in sorted(rows, key=lambda x: (float(x["start_time"]), float(x["end_time"]))):
        start, end = float(r["start_time"]), float(r["end_time"])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise ValueError("invalid segment extent")
        if not isinstance(r["speaker"], (str, int)) or isinstance(r["speaker"], bool):
            raise ValueError("speaker must be explicit")
        key = str(r["speaker"])
        if not key:
            raise ValueError("empty speaker")
        result.setdefault(key, []).extend(words(r["words"]))
    return {k: v for k, v in result.items() if v}


def score(reference, hypothesis):
    ref, hyp = streams(reference), streams(hypothesis)
    if not ref or len(ref) > 4 or len(hyp) > 4:
        raise ValueError("bounded engineering scorer supports 1..4 reference and 0..4 hypothesis speakers")
    n = max(len(ref), len(hyp))
    r = list(ref.items()) + [(None, [])] * (n - len(ref))
    h = list(hyp.items()) + [(None, [])] * (n - len(hyp))
    best = None
    for order in itertools.permutations(range(n)):
        errors = [distance(r[i][1], h[j][1]) for i, j in enumerate(order)]
        candidate = (sum(errors), order, errors)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    total, order, errors = best
    den = sum(len(v) for v in ref.values())
    per = [{"reference_speaker": r[i][0], "hypothesis_track": h[j][0],
            "reference_words": len(r[i][1]), "errors": errors[i],
            "wer": errors[i] / len(r[i][1]) if r[i][1] else None}
           for i, j in enumerate(order)]
    return {"cpwer": total / den, "errors": total, "reference_words": den,
            "reference_speakers": len(ref), "hypothesis_speakers": len(hyp),
            "speakers": per}


def evaluate(panel, hypotheses):
    cases = {x["id"]: x for x in panel["cases"]}
    if len(cases) != len(panel["cases"]) or set(hypotheses) != set(cases):
        raise ValueError("duplicate, missing or unexpected case")
    results = {}
    for name, case in cases.items():
        s = score(case["reference"], hypotheses[name])
        s["passed"] = (s["cpwer"] <= panel["gate"]["max_case_cpwer"] and
                       s["hypothesis_speakers"] == s["reference_speakers"] and
                       all(x["wer"] is not None and x["wer"] <= panel["gate"]["max_speaker_wer"]
                           for x in s["speakers"]))
        results[name] = s
    return {"passed": all(x["passed"] for x in results.values()), "cases": results,
            "scope": "recorded engineering panel; not UID, DER, natural overlap, hardware or installed qualification"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--panel", type=Path, required=True)
    p.add_argument("--hypotheses", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = evaluate(json.loads(a.panel.read_text()), json.loads(a.hypotheses.read_text()))
    with a.output.open("x") as f:
        json.dump(result, f, indent=2, allow_nan=False)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
