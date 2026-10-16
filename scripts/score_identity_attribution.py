"""Grade utterance attribution separately from recognition coverage.

Reference text is independent ground truth, never generated from the tested
recognizer. Resolve track permutation from text before examining UUIDs. An
ambiguous text assignment cannot prove attribution. This finite recorded-test
scorer does not establish physical audio, population accuracy or authorization.
"""
import itertools
import math

from scripts.score_speaker_aware import distance, words


def evaluate(reference, finals, observations, expected, *, max_speaker_wer, max_case_wer=.25):
    if (not reference or len(reference) > 4 or len(finals) > 4 or
            not math.isfinite(max_speaker_wer) or not 0 <= max_speaker_wer <= 1 or
            not math.isfinite(max_case_wer) or not 0 <= max_case_wer <= 1):
        raise ValueError('bounded reference and explicit word-error limit required')
    if set(reference) != set(expected) or any(not words(t) for t in reference.values()):
        raise ValueError('each reference speaker requires text and an expected UUID or None')
    ids = [v for v in expected.values() if v is not None]
    if any(not isinstance(v, str) or not v for v in ids) or len(ids) != len(set(ids)):
        raise ValueError('distinct reference people require distinct UUIDs')
    joins = {}
    for row in finals:
        key = (row['session_id'], row['sequence'], row['track_id'])
        if not key[2] or key in joins or not words(row['text']):
            raise ValueError('duplicate or empty final')
        joins[key] = []
    for row in observations:
        key = (row['session_id'], row['refers_to'], row['track_id'])
        if key not in joins:
            raise ValueError('observation names no exact final')
        joins[key].append(row)
    if any(len(v) != 1 for v in joins.values()):
        raise ValueError('each final needs exactly one observation')
    if len(finals) != len(reference):
        return dict(attribution_passed=False, text_passed=False,
                    reason='missing_or_extra_track', resolved=0, abstained=0, rows=[])
    speakers = list(reference)
    refs = [words(reference[s]) for s in speakers]
    hypotheses = [words(f['text']) for f in finals]
    assignments = sorted((sum(distance(refs[i], hypotheses[j]) for i, j in enumerate(order)), order)
                         for order in itertools.permutations(range(len(finals))))
    if len(assignments) > 1 and assignments[0][0] == assignments[1][0]:
        return dict(attribution_passed=False, text_passed=False,
                    reason='ambiguous_text_assignment', resolved=0, abstained=0, rows=[])
    rows = []
    for i, j in enumerate(assignments[0][1]):
        final = finals[j]
        observation = joins[(final['session_id'], final['sequence'], final['track_id'])][0]
        uid = observation.get('speaker_uuid')
        if uid is not None and (not isinstance(uid, str) or not uid):
            raise ValueError('invalid reported UUID')
        abstained = uid is None
        wer = distance(refs[i], hypotheses[j]) / len(refs[i])
        # WER alone can hide a few words copied from the other speaker. Refuse
        # reference-exclusive two-word phrases on a named track. This is a
        # finite-panel leakage check, not a universal word-alignment proof.
        pairs = lambda tokens: set(zip(tokens, tokens[1:]))
        own = pairs(refs[i])
        foreign = set().union(*(pairs(r) for k, r in enumerate(refs) if k != i)) - own
        leakage = bool(pairs(hypotheses[j]) & foreign)
        rows.append(dict(reference_speaker=speakers[i], track_id=final['track_id'], wer=wer,
                         abstained=abstained, correct_uuid=not abstained and uid == expected[speakers[i]],
                         foreign_phrase=leakage,
                         attribution_passed=abstained or (uid == expected[speakers[i]] and not leakage
                                                        and wer <= max_speaker_wer),
                         reason=observation.get('reason')))
    cpwer = assignments[0][0] / sum(map(len, refs))
    text_passed = cpwer <= max_case_wer and all(r['wer'] <= max_speaker_wer for r in rows)
    return dict(attribution_passed=all(r['attribution_passed'] for r in rows),
                text_passed=text_passed, cpwer=cpwer, reason='graded', rows=rows,
                resolved=sum(not r['abstained'] for r in rows),
                abstained=sum(r['abstained'] for r in rows))
