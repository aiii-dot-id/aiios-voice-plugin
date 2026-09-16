import copy
import json
from pathlib import Path
import zipfile

import pytest

from scripts.audit_native_tts_priority import ORDER, PRIORITIES, distribution, measurements, validate_rows

ROOT=Path(__file__).resolve().parents[1]


def fixture():
    with zipfile.ZipFile(ROOT/'deliverables/native-tts-gpu-prefix-windows-20260913-r1/windows-evidence.zip') as z:
        rows=json.loads(z.read('run/result.json'))['runs'][0]['rows']
    runs=[]
    for arm in ORDER:
        current=copy.deepcopy(rows)
        for i in (0,1,2,4):current[i]['priority_class']=PRIORITIES[arm]
        runs.append({'arm':arm,'rows':current})
    return runs


@pytest.mark.parametrize('damage',[None,'priority','missing','bool','cancel','stale','phase'])
def test_real_trace_rejects_false_priority_and_control_evidence(damage):
    row=fixture()[0]['rows'];validate_rows(row,'below')
    if damage=='priority':row[1]['priority_class']=32
    elif damage=='missing':del row[1]['priority_class']
    elif damage=='bool':row[1]['priority_class']=True
    elif damage=='cancel':row[3]['observed_computing']=False
    elif damage=='stale':row[3]['stale_samples']=1
    elif damage=='phase':row[1]['profile_ns'][0]=-1
    else:return
    with pytest.raises((AssertionError,KeyError)):validate_rows(row,'below')


def test_variability_is_not_hidden_by_matching_means():
    runs=fixture();assert measurements(runs)['all_observed_spreads_within_5_percent']
    # Same mean, large spread: averaging cannot certify a stable baseline.
    for run,ratio in zip((r for r in runs if r['arm']=='normal'),(.7,1.3,.7,1.3)):
        run['rows'][1]['seconds']*=ratio
    result=measurements(runs)
    assert abs(result['normal_below_mean_ratios']['first_seconds']-1)<1e-12
    assert not result['all_observed_spreads_within_5_percent']
    assert result['arms']['normal']['first_seconds']['max_min_ratio']>1.8


@pytest.mark.parametrize('damage',['short','reordered','extra'])
def test_every_frozen_run_required_in_order(damage):
    runs=fixture()
    if damage=='short':runs.pop()
    elif damage=='reordered':runs[0],runs[1]=runs[1],runs[0]
    else:runs.append(copy.deepcopy(runs[-1]))
    with pytest.raises(AssertionError):measurements(runs)


@pytest.mark.parametrize('bad',[0,-1,float('nan'),float('inf'),True])
def test_bad_timings_refused(bad):
    with pytest.raises(AssertionError):distribution([1,1,1,bad])
