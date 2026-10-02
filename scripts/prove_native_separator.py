"""Verify native separator parity, actual provider execution and cancellation.

Uses the exporter's exact input/reference hashes. This is a component gate,
not installed, acoustic, attribution or physical accelerator qualification.
Core ML profiling proves provider execution, not its internal GPU/ANE choice.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

PROVIDERS = {'cpu': 'CPUExecutionProvider', 'cuda': 'CUDAExecutionProvider',
             'coreml-gpu': 'CoreMLExecutionProvider', 'coreml-ane': 'CoreMLExecutionProvider'}


def digest(path):
    with path.open('rb') as stream:
        h = hashlib.sha256()
        for data in iter(lambda: stream.read(1024*1024), b''):
            h.update(data)
        return h.hexdigest()


def bounded(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def validate(rows, events, samples, provider, returncode, threads=None):
    """Raise on missing, reordered, cancelled-before-execution or fallback proof."""
    if returncode != 0 or not samples or len(rows) != 3*len(samples):
        raise ValueError('native execution incomplete or failed')
    runs = sorted((e for e in events if e.get('name') == 'model_run'), key=lambda e: e['ts'])
    if len(runs) != len(rows):
        raise ValueError('profile does not cover every baseline, cancellation and recovery')
    kernels = [e for e in events if e.get('args', {}).get('provider')]
    runtime = None
    evidence = []
    for case, n in enumerate(samples):
        for position in range(3):
            row, run = rows[3*case+position], runs[3*case+position]
            if type(row.get('case')) is not int or row['case'] != case:
                raise ValueError('case order changed')
            if position == 1:
                if (row.get('cancelled') is not True or
                    not bounded(row.get('cancel_requested_after_ms'), 20, 250) or
                    not bounded(row.get('cancel_admission_ms'), 0, 50) or
                    not bounded(row.get('cancel_retirement_ms'), 0, 250)):
                    raise ValueError('active inference cancellation failed or exceeded bounds')
            else:
                if (row.get('repeat') != position//2 or row.get('samples') != n or
                    not bounded(row.get('relative_l2'), 0, 2e-4) or
                    not bounded(row.get('inference_seconds'), 0, 300) or
                    not isinstance(row.get('runtime_version'), str) or not row['runtime_version']):
                    raise ValueError('baseline or recovery differs from untouched reference')
                runtime = runtime or row['runtime_version']
                if runtime != row['runtime_version']:
                    raise ValueError('runtime changed within one session')
                if threads is not None and row.get('intra_op_threads') != threads:
                    raise ValueError('native thread configuration differs')
            if not bounded(run.get('ts'), 0, 1e15) or not bounded(run.get('dur'), 1, 3e8):
                raise ValueError('invalid profile extent')
            active = [e for e in kernels if run['ts'] <= e.get('ts', -1) < run['ts']+run['dur']]
            counts = {}
            for e in active:
                ep = e['args']['provider']
                counts[ep] = counts.get(ep, 0)+1
            if not counts.get(PROVIDERS[provider]):
                raise ValueError('requested provider did not execute in every run')
            evidence.append({'case': case, 'phase': ('baseline', 'cancel', 'recovery')[position],
                             'profile_duration_us': run['dur'], 'kernel_counts': counts})
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('binary', 'model', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--provider', choices=PROVIDERS, required=True)
    parser.add_argument('--threads', type=int, choices=range(1, 65), default=4)
    args = parser.parse_args()
    args.out.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = {'passed': False, 'scope': 'native separator numerical and cancellation component gate',
              'provider_requested': args.provider, 'intra_op_threads': args.threads}
    started = time.monotonic()
    try:
        manifest = json.loads((args.model/'result.json').read_text())
        if manifest.get('passed') is not True or not manifest.get('parity'):
            raise ValueError('export did not pass')
        graph = args.model/'separator.onnx'
        if digest(graph) != manifest['graph_sha256']:
            raise ValueError('graph binding changed')
        result.update(graph_sha256=digest(graph), binary_sha256=digest(args.binary),
                      reference_manifest_sha256=digest(args.model/'result.json'),
                      gate_sha256=digest(Path(__file__)))
        command = [str(args.binary.resolve()), str(graph.resolve()), args.provider,
                   str((args.out/'profile').resolve())]
        samples = []
        for row in manifest['parity']:
            n = row['samples']
            if type(n) is not int or not 32000 <= n <= 480000 or n in samples:
                raise ValueError('invalid or repeated fixture extent')
            samples.append(n)
            for prefix, count in (('input', n), ('reference', 2*n)):
                path = args.model/f'{prefix}-{n}.f32'
                if path.stat().st_size != count*4 or digest(path) != row[prefix+'_sha256']:
                    raise ValueError('fixture binding changed')
                command.append(str(path.resolve()))
        command.extend(['--cancel', '--threads', str(args.threads)])
        with (args.out/'stdout.log').open('x') as out, (args.out/'stderr.log').open('x') as err:
            completed = subprocess.run(command, stdout=out, stderr=err, timeout=300)
        result['returncode'] = completed.returncode
        rows = [json.loads(line) for line in (args.out/'stdout.log').read_text().splitlines() if line.startswith('{')]
        result['cases'] = rows
        if completed.returncode != 0:
            raise ValueError(f'native executable exited with status {completed.returncode}; see stderr.log')
        profiles = list(args.out.glob('profile*.json'))
        if len(profiles) != 1:
            raise ValueError('one completed session profile required')
        result['profile_sha256'] = digest(profiles[0])
        result['execution'] = validate(rows, json.loads(profiles[0].read_text()),
                                       samples, args.provider, completed.returncode, args.threads)
        result['passed'] = True
    except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as error:
        # Keep logs and a terminal failure, including a killed/reaped timeout.
        result['failure'] = str(error)
    finally:
        result['elapsed_seconds'] = time.monotonic()-started
        with (args.out/'result.json').open('x') as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
    print(json.dumps(result, allow_nan=False))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
