"""Build eight runtime-bound maintenance carriers from public module source.

Populate the pinned module cache first. This command does not download runtime
or model assets, execute workers, sign artifacts, or assemble/install a package.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_plugin_carrier import inputs, verify_sdk, regular_files


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(go, output):
    pin, sdk_files = verify_sdk()
    require(pin['version'] == 'v0.1.15', 'maintenance SDK version')
    version = subprocess.check_output([str(go), 'version'], text=True).strip()
    require(version.startswith('go version go1.27.0 '), 'maintenance carrier requires Go 1.27.0')
    before = inputs()
    matrix_path = ROOT / 'maintenance/carrier-matrix.json'
    matrix_raw = matrix_path.read_bytes()
    matrix = json.loads(matrix_raw)
    expected = {
        'macos-arm64-full': ('darwin', 'arm64'),
        'macos-arm64-small': ('darwin', 'arm64'),
        'linux-x86_64-full': ('linux', 'amd64'),
        'linux-x86_64-small': ('linux', 'amd64'),
        'linux-x86_64-small-cpu': ('linux', 'amd64'),
        'windows-x86_64-full': ('windows', 'amd64'),
        'windows-x86_64-small': ('windows', 'amd64'),
        'windows-x86_64-small-cpu': ('windows', 'amd64'),
    }
    require(set(matrix) == set(expected), 'eight variant matrix')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('GIT_', 'AII_', 'OPENAI_', 'ANTHROPIC_', 'GOOGLE_', 'GEMINI_'))}
    env.update(GOTOOLCHAIN='local', GOWORK='off', GOPROXY='off',
               GOFLAGS='-p=2 -mod=readonly', GOMAXPROCS='2', CGO_ENABLED='0')
    module = json.loads(subprocess.check_output(
        [str(go), 'list', '-m', '-json', 'github.com/aiii-dot-id/aii-plugin-sdk'],
        cwd=ROOT / 'plugin/native', env=env))
    require(module['Version'] == pin['version'] and not module.get('Replace'), 'public module without replacement')
    module_source = Path(module['Dir'])
    require(regular_files(module_source) == sdk_files, 'module cache differs from sealed public SDK source')
    recipe = digest(Path(__file__))
    output.mkdir(parents=True, exist_ok=False)
    record = {'complete': False, 'qualified': False, 'toolchain': version,
              'go_sha256': digest(go), 'sdk_version': pin['version'],
              'sdk_public_revision': pin['revision'], 'module_sum': module['Sum'],
              'source_inputs': before, 'matrix_sha256': hashlib.sha256(matrix_raw).hexdigest(),
              'recipe_sha256': recipe, 'artifacts': {},
              'scope': 'Eight rebuilt Go wrappers. Native engine archives and models are retained separately without modification. No execution/admission/signature claim.'}
    try:
        for variant, target in expected.items():
            row = matrix[variant]
            require((row['goos'], row['goarch']) == target, 'target mismatch')
            profile = row['runtime_profile_sha256']
            require(re.fullmatch('[0-9a-f]{64}', profile) is not None, 'runtime profile hash')
            folder = output / variant
            folder.mkdir()
            artifact = folder / ('plugin.exe' if target[0] == 'windows' else 'plugin')
            command = [str(go), 'build', '-trimpath', '-buildvcs=false', '-ldflags',
                       '-X main.packagedRuntimeSHA=' + profile, '-o', str(artifact), '.']
            with (folder / 'build.stdout').open('xb') as stdout, (folder / 'build.stderr').open('xb') as stderr:
                result = subprocess.run(command, cwd=ROOT / 'plugin/native',
                                        env={**env, 'GOOS': target[0], 'GOARCH': target[1]},
                                        stdout=stdout, stderr=stderr, timeout=600)
            require(result.returncode == 0, 'carrier build failed: ' + variant)
            info = subprocess.check_output([str(go), 'version', '-m', str(artifact)], text=True)
            require('\tdep\tgithub.com/aiii-dot-id/aii-plugin-sdk\tv0.1.15\t' + module['Sum'] in info,
                    'built carrier SDK version/hash mismatch')
            require('\t=>\t' not in info and '-trimpath=true' in info, 'carrier replace/private-path risk')
            (folder / 'buildinfo.txt').write_text(info)
            record['artifacts'][variant] = {'sha256': digest(artifact), 'bytes': artifact.stat().st_size,
                'runtime_profile_sha256': profile, 'goos': target[0], 'goarch': target[1],
                'stdout_sha256': digest(folder / 'build.stdout'), 'stderr_sha256': digest(folder / 'build.stderr'),
                'buildinfo_sha256': digest(folder / 'buildinfo.txt')}
        require(before == inputs() and matrix_path.read_bytes() == matrix_raw and digest(Path(__file__)) == recipe,
                'source changed during carrier builds')
        require(regular_files(module_source) == sdk_files, 'public module changed during builds')
        record['complete'] = True
    finally:
        (output / 'BUILD.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'complete': record['complete'], 'qualified': False, 'carriers': len(record['artifacts'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--go', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.go.resolve(), args.output.resolve())
