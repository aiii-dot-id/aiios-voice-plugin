from scripts._assertions import require_assertions
require_assertions()
import json, os, queue, signal, sys, time
from pathlib import Path
from scripts.measure_engine_m1 import Engine, ModelServer

runtime, models, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=False)
result = {}
for case in ('mid_reply', 'idle'):
    server = ModelServer(runtime, models, (out / f'{case}-server.log').open('wb'))
    e = Engine(runtime, models, (out / f'{case}-session.log').open('wb'), server)
    if case == 'mid_reply':
        sid = 'lost-1'
        e.session(sid)
        e.call('speech.session.synthesize', session_id=sid, synthesis_id='r-1',
               text='This reply is long enough that the model server is lost while it is being spoken. ' * 4)
        time.sleep(0.5)
    killed = time.monotonic()
    os.kill(server.p.pid, signal.SIGKILL)
    server.p.wait()
    failure = None
    until = time.monotonic() + 30
    while case == 'mid_reply' and failure is None and time.monotonic() < until:
        try:
            _, ev = e.events.get(timeout=.05)
        except queue.Empty:
            continue
        if ev['type'] == 'failure':
            failure = ev
    try:
        code = e.p.wait(timeout=30)
    except Exception:
        code = 'still running'
    row = {'session_process_exit': code, 'ended_after_ms': round((time.monotonic() - killed) * 1000)}
    if failure:
        row.update(reason=failure.get('reason'), reason_code=failure.get('reason_code'),
                   retry_may_pass=failure.get('retry_may_pass'))
    log = (out / f'{case}-session.log').read_text(errors='replace')
    row['said_on_stderr'] = 'model server lost' in log
    result[case] = row
    server.control.close()
print(json.dumps(result, indent=2))
