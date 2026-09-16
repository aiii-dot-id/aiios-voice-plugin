import copy
import json
from pathlib import Path

import pytest

from scripts.audit_asr_dml_full_loop import read_zip
from scripts.audit_carrier_retirement_trace import validate, digest
from scripts.audit_frozen_windows_desktop import PRIOR, PRIOR_SHA

BASE=Path(__file__).resolve().parents[1]/'deliverables/windows-carrier-retirement-20260915-r1'


@pytest.fixture(scope='module')
def inputs():
    c=json.loads((BASE/'result-r1/collection.json').read_text())
    o=json.loads((BASE/'result-r1/owner.json').read_text())
    return (read_zip(BASE/'transfer/source.zip',o['archive_sha256']),
            read_zip(BASE/'result-r1/evidence.zip',c['receipt']['sha256']),read_zip(PRIOR,PRIOR_SHA))


def test_complete_trace_is_evidence_not_release_qualification(inputs):
    r=validate(*inputs)
    assert r['evidence_valid'] and not r['qualified'] and r['cases_with_exact_pcm']==26


@pytest.mark.parametrize('change', ('qualification','native_bytes','lost_retirement','duplicate_phase','audio','source_change'))
def test_false_diagnostic_closure_is_refused(inputs,change):
    source,original,prior=inputs; evidence=copy.deepcopy(original)
    def edit(path,modify):
        r=json.loads(evidence[path]);modify(r);evidence[path]=json.dumps(r).encode()
    if change=='qualification': edit('run/result.json',lambda r:r.update(qualified=True))
    elif change=='native_bytes': edit('run/checkpoint/freeze.json',lambda r:r['library_hashes'].update({'invented.dll':'0'*64}))
    elif change=='lost_retirement': edit('retirement.json',lambda r:r['absent_pids'].pop())
    elif change=='source_change': edit('run/checkpoint/carrier-build.json',lambda r:r['inputs'].update({'extra.go':'0'*64}))
    elif change=='audio':
        path=next(n for n in evidence if n.startswith('run/settings-1/') and n.endswith('.wav'))
        evidence[path]=evidence[path][:-2]+b'XX'
    else:
        path='run/settings-1/owner/worker.stderr.log'
        line=next(x for x in evidence[path].splitlines() if b'retirement-carrier-cleanup-begin' in x)
        evidence[path]+=b'\n'+line+b'\n'
    with pytest.raises(AssertionError): validate(source,evidence,prior)
