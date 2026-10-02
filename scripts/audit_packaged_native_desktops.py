"""Audit raw zero-argument desktop checkpoint evidence; no installation claim."""
import argparse
import io
import json
from pathlib import Path
import wave

from scripts.audit_native_enrollment_desktops import analyze, archive, digest, sdk_identity


def bound_result(result, frozen, carrier, retirement):
    assert result['passed'] and result['exit_code'] == 0
    assert result.get('launch_command', result.get('command')) == [carrier]
    assert result['checkpoint']['runtime_manifest_sha256'] == frozen['runtime_manifest_sha256']
    loaded = result['loaded_worker']
    assert {loaded['pid'], loaded['parent_pid']} <= set(retirement)
    assert set(loaded['bound_images']) == set(frozen['library_hashes'])
    for name, row in loaded['bound_images'].items():
        assert row['sha256'] == frozen['library_hashes'][name]
    bindings = {n.replace('\\', '/'): h for n, h in result['bindings'].items()}
    root = str(Path(carrier).parent).replace('\\', '/') if '\\' not in carrier else carrier.replace('\\', '/').rsplit('/', 1)[0]
    assert bindings[carrier.replace('\\', '/')] == frozen['carrier_sha256']
    assert bindings[root + '/voice-runtime.json'] == frozen['runtime_manifest_sha256']
    data = frozen['models_root'].replace('\\', '/')
    assert len(frozen['models']) == 24
    for name, row in frozen['models'].items():
        assert bindings[data + '/' + name] == row['sha256']


def settings_evidence(read, prefix, result):
    cases = result['cases']
    assert len(cases) == 13 and len({c['name'] for c in cases}) == 13
    voices = {'alba', 'marius', 'javert', 'fantine', 'eponine', 'azelma',
              'bill_boerst', 'peter_yearsley', 'stuart_bell', 'caro_davy'}
    assert {c['name'] for c in cases} == voices | {'alba-repeat', 'alba-new-seed', 'alba-new-temperature'}
    outputs = {}
    for case in cases:
        raw = read(prefix + case['name'] + '.wav')
        assert digest(raw) == case['wav_sha256']
        with wave.open(io.BytesIO(raw)) as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == (24000, 1, 2, case['samples'])
            pcm = w.readframes(w.getnframes())
        assert len(pcm) == case['samples'] * 2, 'settings waveform has a missing PCM tail'
        assert case['samples'] > 24000 and any(pcm)
        assert digest(pcm) == case['pcm_sha256']
        outputs[case['name']] = digest(pcm)
        settings = case['effective']
        assert set(settings) == {'tts_voice', 'tts_language', 'stt_language', 'turn_pause_ms', 'vad_threshold', 'tts_temperature', 'tts_seed'}
        assert settings['tts_language'] == settings['stt_language'] == 'en'
        expected_voice = case['name'] if case['name'] in voices else 'alba'
        assert settings['tts_voice'] == expected_voice
        modified = case['name'] in ('alba-new-seed', 'alba-new-temperature')
        assert settings['turn_pause_ms'] == (768 if modified else 1200)
        assert abs(settings['vad_threshold'] - (.5 if modified else .65)) < 1e-6
        assert abs(settings['tts_temperature'] - (.7 if case['name'] == 'alba-new-temperature' else .3)) < 1e-6
        assert settings['tts_seed'] == (7 if case['name'] == 'alba-new-seed' else 20260908)
    assert len({outputs[v] for v in voices}) == 10
    assert outputs['alba'] == outputs['alba-repeat']
    assert outputs['alba'] != outputs['alba-new-seed'] != outputs['alba-new-temperature']
    assert outputs['alba'] != outputs['alba-new-temperature']
    return {'voices': 10, 'cases': 13, 'languages': ['en'],
            'warm_short_reply_rtf_range': [min(c['rtf'] for c in cases), max(c['rtf'] for c in cases)]}


def main():
    p = argparse.ArgumentParser()
    for n in ('source', 'linux', 'windows', 'out'):
        p.add_argument('--' + n, type=Path, required=True)
    a = p.parse_args()
    assert not a.out.exists()
    source, files = archive(a.source)
    pin = json.loads(source.read('plugin/sdk-source.json'))
    assert digest(source.read(pin['archive'])) == pin['archive_sha256']
    report = {'passed': False, 'scope': __doc__, 'signed': False, 'installed': False,
              'human_level_qualified': False, 'sdk_revision': pin['revision'], 'platforms': {},
              'source_sha256': digest(a.source.read_bytes())}
    for platform, path, prefix in [('linux', a.linux, ''), ('windows', a.windows, 'run/')]:
        z, inventory = archive(path)
        get = lambda n: json.loads(z.read(prefix + n))
        terminal = get('complete.json')
        assert terminal['passed'] and terminal['platform'] == platform
        assert terminal['signed'] is False and terminal['installed'] is False
        stages = get('post-preparation.json')
        assert stages['passed'] and 'active' not in stages
        assert [s['name'] for s in stages['stages']] == ['freeze', 'enrollment', 'settings']
        assert all(s['exit_code'] == 0 and s['retired'] for s in stages['stages'])
        retired = json.loads(z.read('retirement.json'))['absent_pids']
        assert {s['pid'] for s in stages['stages']} <= set(retired)
        frozen, build, profile = [get('checkpoint/' + n) for n in ('freeze.json', 'carrier-build.json', 'runtime/voice-runtime.json')]
        assert frozen['passed'] and not frozen['signed'] and not frozen['installed']
        assert profile['platform'] == frozen['platform'] == platform
        assert profile['backend'] == 'native' and frozen['backend'] == 'vulkan'
        assert not profile['python'] and not profile['bootstrap'] and not profile['site']
        assert digest(z.read(prefix + 'checkpoint/runtime/voice-runtime.json')) == frozen['runtime_manifest_sha256'] == build['runtime_manifest_sha256']
        assert build['carrier_sha256'] == frozen['carrier_sha256'] and build['sdk_revision'] == pin['revision']
        assert frozen['runtime_files'] == len(profile['files']) + 2
        assert frozen['runtime_bytes'] == sum(v['bytes'] for v in profile['files'].values()) + len(z.read(prefix + 'checkpoint/runtime/voice-runtime.json')) + build['carrier_bytes']
        for name, h in build['inputs'].items():
            assert files[name]['sha256'] == h
        assert sum(v['bytes'] for v in frozen['models'].values()) == frozen['model_bytes']
        for name, h in frozen['library_hashes'].items():
            matching = [v['sha256'] for n, v in profile['files'].items() if n.rsplit('/', 1)[-1].lower() == name]
            assert matching == [h]
        enrollment, settings = get('sdk/result.json'), get('settings/result.json')
        carrier = enrollment['checkpoint']['root'] + ('\\runtime\\aii-voice-t3.exe' if platform == 'windows' else '/runtime/aii-voice-t3')
        for result in (enrollment, settings):
            bound_result(result, frozen, carrier, retired)
            normalize = lambda n: n.replace('\\', '/').removeprefix('//?/')
            bindings = {normalize(n): h for n, h in result['bindings'].items()}
            runtime_root = normalize(result['checkpoint']['root']) + '/runtime'
            for name, row in profile['files'].items():
                assert bindings[runtime_root + '/' + name] == row['sha256']
            # Same bytes from a development library directory are not a
            # relocated-package proof. Bound loaded images must be in runtime.
            for name, row in result['loaded_worker']['bound_images'].items():
                expected = next(n for n in profile['files'] if n.rsplit('/', 1)[-1].lower() == name)
                observed_path, expected_path = normalize(row['path']), runtime_root + '/' + expected
                if platform == 'windows': observed_path, expected_path = observed_path.casefold(), expected_path.casefold()
                assert observed_path == expected_path, ('external loaded image', row['path'])
        assert sdk_identity(enrollment, pin)
        for name in files:
            if name.startswith(('runtime/native/session/', 'runtime/native_uid/', 'plugin/native/')):
                candidates = [h for n, h in enrollment['bindings'].items() if n.replace('\\', '/').endswith('/source/' + name)]
                assert candidates == [files[name]['sha256']], name
        observed = analyze(z.read, prefix + 'sdk/', enrollment)
        observed['settings'] = settings_evidence(z.read, prefix + 'settings/', settings)
        observed.update(runtime_bytes=frozen['runtime_bytes'], runtime_files=frozen['runtime_files'],
                        model_bytes=frozen['model_bytes'], runtime_manifest_sha256=frozen['runtime_manifest_sha256'],
                        carrier_sha256=frozen['carrier_sha256'], checkpoint_root=enrollment['checkpoint']['root'],
                        absent_pids=retired, evidence_sha256=digest(path.read_bytes()))
        report['platforms'][platform] = observed
    report['passed'] = True
    with a.out.open('x') as f:
        json.dump(report, f, indent=2); f.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
