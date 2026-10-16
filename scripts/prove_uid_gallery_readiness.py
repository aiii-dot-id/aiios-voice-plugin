"""Native decision regression on copied galleries and frozen query embeddings.

No model training, live identity writes or installed/browser qualification.
Inputs and detailed outputs are private evidence; only counts are printed.
Each query gets a fresh in-memory test host, preventing query-to-query training.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tally(answers, expected):
    return dict(queries=len(answers),
                target_matches=sum(a.get('speaker_id') == expected and a.get('outcome') == 'known'
                                   for a in answers),
                other_named_matches=sum(bool(a.get('speaker_id')) and a.get('speaker_id') != expected
                                        for a in answers),
                anonymous_matches=sum(bool(a.get('speaker_uuid')) for a in answers),
                invalid_decisions=sum((bool(a.get('speaker_id')) and a.get('outcome') != 'known') or
                                      (a.get('outcome') == 'known' and not a.get('speaker_id'))
                                      for a in answers))


def passed(arm):
    positive, negative = arm['positive'], arm['negative']
    return (positive['queries'] > 0 and negative['queries'] > 0 and
            positive['target_matches'] == positive['queries'] and
            positive['invalid_decisions'] == negative['invalid_decisions'] == 0 and
            positive['other_named_matches'] == positive['anonymous_matches'] == 0 and
            negative['target_matches'] == negative['other_named_matches'] == negative['anonymous_matches'] == 0)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    names = ('probe', 'baseline-probe', 'policy', 'enrollment', 'registry', 'positive', 'negative', 'output')
    for name in names:
        p.add_argument('--'+name, type=Path, required=True)
    args = p.parse_args()
    paths = {name: getattr(args, name.replace('-', '_')).resolve()
             for name in names if name != 'output'}
    paths['verifier'] = Path(__file__).resolve()
    bindings = {name: digest(path) for name, path in paths.items()}
    enrolled = json.loads(paths['enrollment'].read_text())['speakers']
    if len(enrolled) != 1:
        raise ValueError('This positive/absent gate requires exactly one reference identity')
    expected = enrolled[0]['id']
    panels = {name: json.loads(paths[name].read_text()) for name in ('positive', 'negative')}
    hashes = set()
    for rows in panels.values():
        if not isinstance(rows, list) or not 1 <= len(rows) <= 4096:
            raise ValueError('Empty or oversized query panel')
        for row in rows:
            key = row['pcm_sha256']
            if key in hashes:
                raise ValueError('Duplicate query evidence across panels')
            hashes.add(key)
    args.output.mkdir(parents=True, exist_ok=False)
    report = dict(scope=__doc__, bindings=bindings, completed=False, passed=False, arms={})
    result_path = args.output / 'result.json'
    try:
        for label, executable in (('baseline', paths['baseline-probe']), ('candidate', paths['probe'])):
            arm = {}
            for name, rows in panels.items():
                answers = []
                for index, row in enumerate(rows):
                    stem = args.output / f'{label}-{name}-{index:04}'
                    query = stem.with_suffix('.input.json')
                    query.write_text(json.dumps([row])+'\n')
                    with stem.with_suffix('.output.json').open('w') as out, stem.with_suffix('.log').open('w') as err:
                        proc = subprocess.run([str(executable), '--observation-panel', str(paths['policy']),
                                               str(paths['enrollment']), str(paths['registry']), str(query)],
                                              stdout=out, stderr=err, timeout=30)
                    if proc.returncode:
                        raise RuntimeError(f'{label}/{name}/{index}: native decision failed')
                    answer = json.loads(stem.with_suffix('.output.json').read_text())
                    if not isinstance(answer, list) or len(answer) != 1:
                        raise ValueError('Native query cardinality differs')
                    answers.extend(answer)
                arm[name] = tally(answers, expected)
            report['arms'][label] = arm
        if bindings != {name: digest(path) for name, path in paths.items()}:
            raise ValueError('Bound decision inputs changed during execution')
        report['passed'] = passed(report['arms']['candidate'])
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        report['completed'] = True
        result_path.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({key: report[key] for key in ('completed', 'passed', 'arms')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
