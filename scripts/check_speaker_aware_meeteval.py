"""Independently check panel scoring against MeetEval's global cpWER."""
import argparse
import json
from pathlib import Path

from meeteval.wer.wer.cp import cp_word_error_rate

from score_speaker_aware import evaluate, streams


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--panel", type=Path, required=True)
    p.add_argument("--hypotheses", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    panel, hypotheses = json.loads(a.panel.read_text()), json.loads(a.hypotheses.read_text())
    scored = evaluate(panel, hypotheses)
    evidence = {}
    for case in panel["cases"]:
        ref = {k: " ".join(v) for k, v in streams(case["reference"]).items()}
        hyp = {k: " ".join(v) for k, v in streams(hypotheses[case["id"]]).items()}
        independent = cp_word_error_rate(ref, hyp, reference_sort=False, hypothesis_sort=False)
        own = scored["cases"][case["id"]]
        equal = independent.errors == own["errors"] and independent.length == own["reference_words"]
        evidence[case["id"]] = dict(agrees=equal, errors=independent.errors,
                                    reference_words=independent.length, cpwer=independent.error_rate)
    result = dict(passed=all(x["agrees"] for x in evidence.values()), cases=evidence,
                  scope="scorer agreement only, not candidate acceptance")
    with a.output.open("x") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
