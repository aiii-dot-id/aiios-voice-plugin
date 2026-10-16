"""Instrument a supplied speech diagnostic without changing its SDK calls.

Requires the historical diagnostic's explicit child anchors; a different source
is refused. No private archive, machine path, or bundled model is assumed.
The generated program needs the supplied diagnostic's dependencies and the
Windows counter readers beside this module. Added idle windows make these
resource observations unsuitable for promotion timing.
"""
import argparse
import ast
import hashlib
from pathlib import Path

def child_program(raw):
    text = raw.decode()
    # Only the private diagnostic child changes: model, SDK calls, fixture and
    # audio assertions remain the parent's implementation. Idle windows are
    # explicit and disqualify this instrumented run from promotion timing.
    tree = ast.parse(text)
    children = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'child']
    if len(children) != 1:
        raise ValueError('one diagnostic child required')
    # A frozen fixture or supplied diagnostic may omit its command-line main.
    mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main']
    if mains:
        if len(mains) != 1 or mains[0].lineno <= children[0].end_lineno:
            raise ValueError('unexpected diagnostic main layout')
        text = ''.join(text.splitlines(keepends=True)[:mains[0].lineno-1])
    for anchor in ('\n    host = None\n', '\n        for repeat in range(2):\n'):
        if text.count(anchor) != 1:
            raise ValueError('diagnostic anchor differs')
    insert = '''    from scripts.windows_voice_cpu import CPUSampler
    from scripts.windows_speech_resources import WindowsResources
    sampler = None
    marks = []
    def mark(name):
        marks.append({'name': name, 'elapsed': time.perf_counter() - host.started})
'''
    text = text.replace('    host = None\n', '    host = None\n' + insert, 1)
    anchor = "        result['loaded_worker'] = observe(host.process.pid, Path(p['worker']).parent, 'windows', p['libraries'])\n"
    if text.count(anchor) != 1:
        raise ValueError('diagnostic anchor differs')
    text = text.replace(anchor, anchor + '''        reader = WindowsResources()
        sampler = CPUSampler(host.started, reader, period=.05)
        sampler.attach('worker', result['loaded_worker']['pid'])
        sampler.attach('carrier', host.process.pid)
        sampler.attach('observer', os.getpid())
        result['resource_identity'] = reader.gpu.identity
        sampler.start()
        mark('idle_before_begin');time.sleep(3);mark('idle_before_end')
''')
    text = text.replace('        for repeat in range(2):\n', "        for repeat in range(2):\n            mark('conversation-'+str(repeat)+'-begin')\n", 1)
    anchor = "            result['conversations'].append({'path': str(path), 'sha256': sha(path)})\n"
    if text.count(anchor) != 1:
        raise ValueError('diagnostic anchor differs')
    text = text.replace(anchor, anchor + "            mark('conversation-'+str(repeat)+'-end')\n            mark('idle_after-'+str(repeat)+'-begin');time.sleep(2);mark('idle_after-'+str(repeat)+'-end')\n")
    anchor = "        result['exit_code'] = host.close()\n"
    if text.count(anchor) != 1:
        raise ValueError('diagnostic anchor differs')
    text = text.replace(anchor, '''        sampler.close()
        result['resources'] = {'rows': sampler.rows, 'marks': marks, 'period_seconds': .05}
        sampler = None
''' + anchor)
    # Retire observation before host shutdown on a failing speech path as well.
    anchor = '    finally:\n        stop.set()\n'
    if text.count(anchor) != 1:
        raise ValueError('diagnostic anchor differs')
    text = text.replace(anchor, '''    finally:
        if sampler:
            try:
                if sampler.thread.ident is None:
                    sampler.reader.close()
                else:
                    sampler.close()
            except Exception as error:
                errors.append('resource retirement: '+repr(error))
            result['resources'] = {'rows': sampler.rows, 'marks': marks, 'period_seconds': .05}
            result['passed'] = False
        stop.set()
''')
    compile(text, 'resource_session_child.py', 'exec')
    return text.encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    raw = args.parent.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.sha256:
        raise ValueError('diagnostic source binding differs')
    generated = child_program(raw)
    with args.out.open('xb') as stream:
        stream.write(generated)


if __name__ == '__main__':
    main()
