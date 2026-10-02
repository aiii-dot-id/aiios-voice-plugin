"""Validate recorded native-session resource counters; not speech qualification.

Accepts a JSON array of sampler rows. The caller owns recording and speech
acceptance. This auditor never treats low utilization as a performance pass.
"""
import argparse
import json
import math
from pathlib import Path
import statistics
from scripts.windows_voice_cpu import summarize

def region(rows, begin, end):
    selected = [r for r in rows if begin <= r['elapsed'] <= end]
    cpu = summarize(rows, begin, end)
    for row in selected:
        if not (set(row['processes']) == {'worker', 'carrier', 'observer'}):
            raise ValueError('invalid resource evidence')
        if not (all(p['exited'] is False for p in row['processes'].values())):
            raise ValueError('invalid resource evidence')
        g = row['gpu']
        for key in ('gpu_percent', 'memory_percent', 'sm_mhz', 'memory_mhz', 'memory_total', 'memory_used', 'memory_free', 'temperature_c', 'pstate'):
            if not (math.isfinite(g[key]) and g[key] >= 0):
                raise ValueError('invalid GPU counter')
        if not (g['gpu_percent'] <= 100 and g['memory_percent'] <= 100 and g['sm_mhz'] > 0):
            raise ValueError('invalid resource evidence')
        if not (g['memory_used'] <= g['memory_total'] and g['memory_free'] <= g['memory_total']):
            raise ValueError('invalid resource evidence')
        m = row['system_memory']
        if not all(math.isfinite(m[k]) for k in ('available_physical_bytes', 'total_physical_bytes',
                                               'available_commit_bytes', 'total_commit_bytes')):
            raise ValueError('nonfinite system memory')
        if not (0 <= m['available_physical_bytes'] <= m['total_physical_bytes'] and m['total_physical_bytes'] > 0):
            raise ValueError('invalid resource evidence')
        if not (0 <= m['available_commit_bytes'] <= m['total_commit_bytes'] and m['total_commit_bytes'] > 0):
            raise ValueError('invalid resource evidence')
        for p in row['processes'].values():
            if not (all(math.isfinite(v) and v >= 0 for v in p['memory'].values())):
                raise ValueError('invalid resource evidence')
            if not (p['memory']['working_bytes'] <= p['memory']['peak_working_bytes']):
                raise ValueError('invalid resource evidence')
    first, last = selected[0], selected[-1]
    faults = {}
    for label in first['processes']:
        counts = [r['processes'][label]['memory']['page_faults'] for r in selected]
        if not (all(b >= a for a, b in zip(counts, counts[1:]))):
            raise ValueError('page faults regressed')
        faults[label] = counts[-1] - counts[0]
    return {'cpu': cpu, 'samples': len(selected), 'page_faults': faults,
        'worker_private_bytes': {'min': min(r['processes']['worker']['memory']['private_bytes'] for r in selected),
                                 'max': max(r['processes']['worker']['memory']['private_bytes'] for r in selected)},
        'worker_working_bytes': {'min': min(r['processes']['worker']['memory']['working_bytes'] for r in selected),
                                 'max': max(r['processes']['worker']['memory']['working_bytes'] for r in selected)},
        'minimum_available_physical_bytes': min(r['system_memory']['available_physical_bytes'] for r in selected),
        'gpu': {k: {'min': min(r['gpu'][k] for r in selected), 'mean': statistics.mean(r['gpu'][k] for r in selected),
                    'max': max(r['gpu'][k] for r in selected)}
                for k in ('sm_mhz', 'memory_mhz', 'gpu_percent', 'memory_percent', 'memory_used', 'temperature_c', 'pstate')},
        'observer_read_wall_fraction': sum(r['read_seconds'] for r in selected) / (last['elapsed'] - first['elapsed'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=Path, required=True)
    parser.add_argument('--begin', type=float, required=True)
    parser.add_argument('--end', type=float, required=True)
    args = parser.parse_args()
    print(json.dumps(region(json.loads(args.rows.read_text()), args.begin, args.end), allow_nan=False))


if __name__ == '__main__':
    main()
