from scripts._assertions import require_assertions
require_assertions()
import argparse, json, queue, threading, time
from pathlib import Path
import scripts.measure_engine_m1 as m1
from scripts.measure_engine_m1 import Engine, ModelServer, resample_24k_to_16k


def started(runtime, models, out, server, tag):
    e = Engine(runtime, models, (out / f'session-{tag}.log').open('wb'), server)
    begun = time.monotonic()
    stream = e.session(f's-{tag}')
    return e, stream, {'tag': tag, 'ready_ms': round(e.ready_s * 1000),
                       'open_ms': round((time.monotonic() - begun) * 1000)}


def observation(e, sid, timeout=10):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            _, ev = e.events.get(timeout=.05)
        except queue.Empty:
            continue
        if ev.get('session_id') == sid and ev['type'] == 'speaker_observation':
            return {'decision': ev.get('decision'), 'reason': ev.get('reason')}
    return None


def main():
    a = argparse.ArgumentParser()
    a.add_argument('runtime', type=Path)
    a.add_argument('models', type=Path)
    a.add_argument('out', type=Path)
    a.add_argument('--processes', type=int, default=4)
    a.add_argument('--language', default='fr', help="another speaking language than English; '' for none")
    args = a.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    server = ModelServer(args.runtime, args.models, (args.out / 'server.log').open('wb'))
    result = {'server_ready_s': server.ready_s, 'alone': [], 'together': [], 'language': []}
    for i in range(args.processes):
        e, stream, row = started(args.runtime, args.models, args.out, server, f'alone-{i}')
        sid = f's-alone-{i}'
        replies, pcm16 = [], None
        for _ in range(3):
            reply, pcm = e.speak(sid)
            replies.append(reply)
            pcm16 = pcm16 or resample_24k_to_16k(pcm)
        final = e.hear(sid, stream, pcm16, 0)
        row.update(first_sound_ms=[r['first_pcm_ms'] for r in replies], rtf=[r['rtf'] for r in replies],
                   reply_sha256=sorted({r['sha256'] for r in replies}), final_ms=final['final_after_end_ms'],
                   text_sha256=final['text_sha256'], speaker=observation(e, sid))
        e.call('speech.session.close', session_id=sid, mode='abort'); e.wait('session_end', sid)
        e.close()
        result['alone'].append(row)
    rows = [None] * args.processes
    def together(i):
        e = Engine(args.runtime, args.models, (args.out / f'session-together-{i}.log').open('wb'), server)
        row = {'tag': f'together-{i}', 'ready_ms': round(e.ready_s * 1000)}
        try:
            e.session(f's-together-{i}')
            e.call('speech.session.close', session_id=f's-together-{i}', mode='abort')
            e.wait('session_end', f's-together-{i}')
            row['opened'] = True
        except AssertionError as refused:
            row['opened'] = False
            row['refused'] = str(refused)[:300]
        e.close()
        rows[i] = row
    threads = [threading.Thread(target=together, args=(i,)) for i in range(args.processes)]
    for t in threads: t.start()
    for t in threads: t.join()
    result['together'] = rows
    for i, language in enumerate(('en', args.language, 'en', args.language, args.language) if args.language else ()):
        m1.VALUES['tts_language'] = language
        e, _, row = started(args.runtime, args.models, args.out, server, f'language-{i}-{language}')
        reply, _ = e.speak(f's-language-{i}-{language}')
        e.call('speech.session.close', session_id=f's-language-{i}-{language}', mode='abort')
        e.wait('session_end', f's-language-{i}-{language}')
        e.close()
        row.update(language=language, first_sound_ms=reply['first_pcm_ms'], reply_sha256=reply['sha256'])
        result['language'].append(row)
    m1.VALUES.pop('tts_language', None)
    server.close()
    (args.out / 'result.json').write_text(json.dumps(result, indent=1))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
