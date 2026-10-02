"""Bind and verify native speaker-conditioned recognition, not a plugin release.

Input remains the reference's pre-encoded microphone features and predicted
speaker masks. This does not qualify a native frontend, diarization, enrolled
person identity, hardware acceleration, or the installed SDK path.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess
import time

from .run_speaker_aware_reference import digest, NEMO_REV, write_json


def verify_graphs(root):
    result=json.loads((root/'result.json').read_text())
    names={'asr_preencode','diar_preencode','asr_encoder','asr_decoder','asr_joiner','diar_classifier'}
    if (not result['passed'] or result['upstream_revision']!=NEMO_REV or
            result['ort_optimization']!='disabled' or not result['blank_equals_start'] or
            {g['name'] for g in result['graphs']}!=names or len(result['graphs'])!=len(names)):
        raise ValueError('unqualified graph set')
    for g in result['graphs']:
        if not g['passed'] or not g['cases'] or not all(c['passed'] for c in g['cases']):
            raise ValueError('failed graph case')
        if g['name'].endswith('preencode') and not g['multiple_captures_refused']:
            raise ValueError('preencoder capture ownership missing')
    declared=result['artifacts']
    present={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if present!=set(declared)|{'result.json'}:raise ValueError('graph artifact census differs')
    for name,claim in declared.items():
        relative=PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name:
            raise ValueError('graph path escapes inventory')
        path=root.joinpath(*relative.parts)
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('graph path escapes root')
        if path.stat().st_size!=claim['bytes'] or digest(path)!=claim['sha256']:
            raise ValueError('graph binding changed')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--graphs',type=Path,required=True)
    p.add_argument('--trace',type=Path,required=True)
    p.add_argument('--probe',type=Path,required=True)
    p.add_argument('--ort-library',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    result=dict(passed=False,scope=__doc__,installed=False,speaker_identification_qualified=False,
                trials=[],process_retired=False)
    started=time.monotonic()
    try:
        verify_graphs(a.graphs)
        trace=json.loads((a.trace/'result.json').read_text())
        if not trace['passed'] or trace['upstream_revision']!=NEMO_REV or trace['records']!=110 or trace['epochs']!=7:
            raise ValueError('incomplete frozen recorded trace')
        for name,key in [('decoder.trace','trace_sha256'),('conditioned.trace','conditioned_trace_sha256')]:
            if digest(a.trace/name)!=trace[key]:raise ValueError('trace binding changed')
        source=Path(__file__).resolve().parents[1]/'runtime/native_multitalker'
        result.update(probe_sha256=digest(a.probe),ort_library_sha256=digest(a.ort_library),
                      verifier_sha256=digest(__file__),graph_result_sha256=digest(a.graphs/'result.json'),
                      trace_result_sha256=digest(a.trace/'result.json'),
                      sources={x.name:digest(x) for x in sorted(source.iterdir()) if x.is_file()})
        for label,file,mutation in [('decoder','decoder.trace',None),('conditioned','conditioned.trace',None),
                                   ('unconditioned','conditioned.trace','--unconditioned'),
                                   ('shared-cache','conditioned.trace','--shared-encoder-cache')]:
            args=[str(a.probe),str(a.graphs),str(a.trace/file)]
            if mutation:args.append(mutation)
            run=subprocess.run(args,text=True,capture_output=True,timeout=300)
            # Only token-level disagreements kill mutations. A missing library,
            # crash, malformed input or timeout is a broken probe, not a kill.
            if mutation:
                passed=run.returncode==1 and run.stderr.startswith(('token count mismatch at record ','token mismatch at record '))
                row=dict(name=label,passed=passed,mutation_killed=passed,exit_code=run.returncode,
                         reason=run.stderr.strip())
            else:
                observed=json.loads(run.stdout) if run.returncode==0 else {}
                passed=(run.returncode==0 and observed.get('passed') and observed.get('records')==110 and
                        observed.get('epochs')==7 and observed.get('tokens')==405 and
                        observed.get('conditioned_encoder')==(label=='conditioned'))
                row=dict(name=label,passed=bool(passed),exit_code=run.returncode,observed=observed,
                         reason=run.stderr.strip())
            result['trials'].append(row)
            write_json(a.out/(label+'.json'),row)
        if digest(a.probe)!=result['probe_sha256'] or digest(a.ort_library)!=result['ort_library_sha256']:
            raise ValueError('executable bytes changed during test')
        verify_graphs(a.graphs)
        result['passed']=all(r['passed'] for r in result['trials'])
        result['process_retired']=True
    except Exception as error:
        result['error']=repr(error)
    finally:
        result['elapsed_seconds']=time.monotonic()-started
        write_json(a.out/'result.json',result)
        print(json.dumps(result),flush=True)
    raise SystemExit(0 if result['passed'] else 1)


if __name__=='__main__':main()
