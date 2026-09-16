"""Collect notices for the exact desktop candidate without changing that candidate.

This is an attribution/evidence bundle, not a license compatibility judgment.
It keeps original notices, binds distribution files to shipped hashes, and
reports unresolved provenance instead of turning a complete file list into a
claim of permission. No weights, models or engines are loaded or downloaded.
"""
import argparse
import base64
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.repackage_native_schemas import read_package
from scripts.uid_native_sources import verified_sources, KNF_SHA, KISS_SHA

PACKAGE_SHA = 'd331620b03ed586a5c2764d236af90a48a60caf8365d827296e0fe004f71cabd'
INVENTORY_SHA = 'ccd12c31e9b0005723b4053b1003f1bc82fcdb9432e5bc6cdaa2926f88f503de'
REMOTE_SHA = 'b7595ebf79d2233a880be2e1a8f6bed4a044ecc2df091cfd8222434631eee216'
POCKET_REV = '3174e6b26f11a0e39b4f150961dce98f43ba860d'
POCKET_SHA = '8a8e0f55e7daa6646b64bfca5942f3b59d06ec3356ee1fe9682e228060684ea6'
TORCH_SHA = '7631ef49fbd38d382909525b83696dc12a55d68492ade4ace3883c62b9fc140f'
SLEEF_REV = '5a1d179df9cf652951b59010a2d2075372d67f68'
SLEEF_SHA = 'afd1b92010ae7918e20eec3e5bb270b7ca4828b5cb19e4d820a0a4558a04ce63'
OPENMP_SHA = '1710356ae0db744ca028ed380759a2007548ad1819f743be9d675603cb127377'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_hash(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def checked_path(path, expected):
    if file_hash(path) != expected:
        raise ValueError('changed source: ' + str(path))
    return path


def check_record(record, member, sha, size):
    """A RECORD member must exist exactly once and bind both content and size."""
    rows = [r for r in csv.reader(io.StringIO(record.decode())) if r and r[0] == member]
    wanted = 'sha256=' + base64.urlsafe_b64encode(bytes.fromhex(sha)).decode().rstrip('=')
    if len(rows) != 1 or len(rows[0]) != 3 or rows[0][1:] != [wanted, str(size)]:
        raise ValueError('distribution RECORD does not bind ' + member)


def check_relocation(row, original_sha, shipped_sha):
    if row.get('source_sha256') != original_sha or row.get('relocated_sha256') != shipped_sha:
        raise ValueError('distribution-to-shipped relocation differs')


def check_version(raw, package, version):
    lines = raw.decode().splitlines()
    if lines.count('Name: ' + package) != 1 or lines.count('Version: ' + version) != 1:
        raise ValueError('wrong distribution name or version')


def distribution_notice_members(names):
    # Intel's required notices are not named LICENSE or NOTICE. Enumerate the
    # distribution's notice-bearing basenames, including its EULA's own term.
    prefixes = ('license', 'notice', 'copying', 'third-party-programs')
    return {n for n in names if PurePosixPath(n).name.lower().startswith(prefixes)}


def model_group(path):
    if path.startswith('tts/embeddings/'):
        return 'pocket-voice-embeddings'
    if path.startswith('tts/'):
        return 'pocket-config' if path.endswith(('.yaml', '.yml')) else 'pocket-tts'
    if path.startswith('stt/'):
        return 'nemotron-asr'
    if path.startswith('vad/'):
        return 'silero-vad'
    if path.startswith('endpoint/'):
        return 'smart-turn'
    if path == 'uid/model.onnx':
        return 'wespeaker-uid'
    raise ValueError('model lacks a notice disposition: ' + path)


class Bundle:
    def __init__(self, out):
        out.mkdir(parents=True, exist_ok=False)
        self.out, self.files = out, {}

    def add(self, name, raw, origin, group):
        p = PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or '\\' in name or str(p) != name or name in self.files:
            raise ValueError('unsafe or duplicate notice path')
        if not raw:
            raise ValueError('empty notice or evidence: ' + name)
        target = self.out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as f:
            f.write(raw)
        self.files[name] = {'sha256': digest(raw), 'bytes': len(raw), 'origin': origin, 'group': group}
        return name

    def local(self, name, path, group):
        return self.add(name, path.read_bytes(), {'local_path': str(path), 'sha256': file_hash(path)}, group)

    def public(self, name, url, group, marker, bound=1024 * 1024):
        req = urllib.request.Request(url, headers={'User-Agent': 'voice-frontier-notice-audit/1'})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read(bound + 1)
            if r.status != 200 or len(raw) > bound:
                raise ValueError('notice download failed or exceeded bound: ' + name)
        if marker not in raw.decode('utf-8'):
            raise ValueError('notice content not recognized: ' + name)
        return self.add(name, raw, {'url': url, 'retrieved_utc': datetime.now(timezone.utc).isoformat(),
                                   'source_kind': 'public text, content sealed at retrieval'}, group)


def model_notices(b):
    hf = 'https://huggingface.co/'
    gh = 'https://raw.githubusercontent.com/'
    sources = [
        ('nemotron-asr/README.md', hf + 'nvidia/nemotron-3.5-asr-streaming-0.6b/raw/1c8deaecc64b91f034d73e08dd8b64625eb3395d/README.md', 'nemotron-asr', 'openmdw'),
        ('pocket-tts/README.md', hf + 'kyutai/pocket-tts-without-voice-cloning/raw/d29db7978e464fb90cb3359ee0c69a273b9142cc/README.md', 'pocket-tts', 'cc-by-4.0'),
        ('pocket-voice-embeddings/README.md', hf + 'kyutai/pocket-tts-without-voice-cloning/raw/e81d79e8194ad4c7ce879c87a4258ef20cbf2487/README.md', 'pocket-voice-embeddings', 'cc-by-4.0'),
        ('pocket-voice-embeddings/reference-sources.md', hf + 'kyutai/tts-voices/raw/323332d33f997de8394f24a193e1a76df720e01a/README.md', 'pocket-voice-embeddings', 'Alba'),
        ('pocket-config/LICENSE', gh + 'kyutai-labs/pocket-tts/896e934690afc0e1047a3667a13514386c1420fc/LICENSE', 'pocket-config', 'Permission'),
        ('silero-vad/README.md', hf + 'onnx-community/silero-vad/raw/e71cae966052b992a7eca6b17738916ce0eca4ec/README.md', 'silero-vad', 'license: mit'),
        ('silero-vad/upstream-LICENSE', gh + 'snakers4/silero-vad/caddb3b7ce1dee88a14d5621a0e9a8fdeb2c2c48/LICENSE', 'silero-vad', 'Silero Team'),
        ('smart-turn/README.md', hf + 'pipecat-ai/smart-turn-v3/raw/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/README.md', 'smart-turn', 'bsd-2-clause'),
        ('smart-turn/upstream-LICENSE', gh + 'pipecat-ai/smart-turn/24c720337e17befe0413bbc93b3504036c3a3bdc/LICENSE', 'smart-turn', 'Daily'),
        ('wespeaker-uid/catalog.md', gh + 'wenet-e2e/wespeaker/dfa741957e5c11f477623b6e583d67d0af25ee88/docs/pretrained.md', 'wespeaker-uid', 'VoxBlink'),
        ('wespeaker-uid/voxblink2-LICENSE', gh + 'VoxBlink2/ScriptsForVoxBlink2/50846d6540783824476e16c006f4a0fe27e70683/LICENSE', 'wespeaker-uid', 'CC BY-NC-SA 4.0'),
        ('wespeaker-uid/voxblink2-model-terms.html', 'https://voxblink2.github.io/', 'wespeaker-uid', 'license of the model is also'),
        ('wespeaker-uid/voxceleb-terms.html', 'https://mm.kaist.ac.kr/datasets/voxceleb/', 'wespeaker-uid', 'Attribution'),
        ('terms/CC-BY-4.0.txt', 'https://creativecommons.org/licenses/by/4.0/legalcode.txt', 'common-terms', 'Attribution 4.0'),
        ('terms/CC-BY-NC-SA-4.0.txt', 'https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.txt', 'common-terms', 'NonCommercial'),
        ('terms/CC0-1.0.txt', 'https://creativecommons.org/publicdomain/zero/1.0/legalcode.txt', 'common-terms', 'CC0 1.0'),
        ('terms/OpenMDW-1.1.html', 'https://openmdw.ai/license/1-1/', 'nemotron-asr', 'OpenMDW'),
        ('asmjit/LICENSE.md', gh + 'asmjit/asmjit/e5d7c0bd5d9aec44d68830187138149e6a8c4e32/LICENSE.md', 'asmjit', 'Copyright'),
    ]
    for name, url, group, marker in sources:
        b.public('notices/' + name, url, group, marker)
    # The Windows wheel has a different Torch git version from the Mac
    # reference. Bind the actual wheel -> its submodules, not a nearby build.
    submodules = [
        ('evidence/torch-fbgemm-submodule.json',
         'https://api.github.com/repos/pytorch/pytorch/contents/third_party/fbgemm?ref=a1cb3cc05d46d198467bebbb6e8fba50a325d4e7',
         '157e88b750c452bef2ab4653fe9d1eeb151ce4c3'),
        ('evidence/fbgemm-asmjit-submodule.json',
         'https://api.github.com/repos/pytorch/FBGEMM/contents/external/asmjit?ref=157e88b750c452bef2ab4653fe9d1eeb151ce4c3',
         'e5d7c0bd5d9aec44d68830187138149e6a8c4e32'),
    ]
    for name, url, sha in submodules:
        b.public(name, url, 'asmjit', sha)
        row = json.loads((b.out / name).read_text())
        if row.get('sha') != sha or not row.get('submodule_git_url'):
            raise ValueError('Torch/AsmJit submodule chain differs')
    b.local('evidence/voice-reference-attribution.md', ROOT / 'deliverables/operator-voice-catalog-20260911-r1/provenance/SOURCE-LICENSES.md', 'pocket-voice-embeddings')
    b.local('evidence/voice-catalog.json', ROOT / 'deliverables/operator-voice-catalog-20260911-r1/result.json', 'pocket-voice-embeddings')
    b.local('evidence/uid-acquisition.json', ROOT / 'research/acquisition/wespeaker-uid.json', 'wespeaker-uid')


def static_notices(b):
    source = ROOT / 'artifacts/native-pocket-source-20260910-r2'
    archive = checked_path(source / 'source.tar.gz', POCKET_SHA)
    selected = ['LICENSE', 'external/ggml/LICENSE', 'external/cJSON/LICENSE', 'external/libyaml/License',
                'external/sentencepiece/LICENSE', 'external/sentencepiece/third_party/esaxx/LICENSE',
                'external/sentencepiece/third_party/protobuf-lite/LICENSE',
                'external/sentencepiece/third_party/absl/LICENSE',
                'external/sentencepiece/third_party/darts_clone/LICENSE']
    with tarfile.open(archive) as t:
        prefix = 'audio.cpp-' + POCKET_REV + '/'
        for name in selected:
            raw = t.extractfile(prefix + name).read()
            # Compare the source tree used by the build, not just its archive.
            if raw != (source / prefix / name).read_bytes():
                raise ValueError('build-tree notice differs from source archive')
            b.add('notices/audio.cpp/' + name, raw,
                  {'archive_sha256': POCKET_SHA, 'revision': POCKET_REV, 'member': prefix + name}, 'audio.cpp-static')
        name = 'external/ggml/src/ggml-cpu/llamafile/sgemm.cpp'
        raw = t.extractfile(prefix + name).read()
        lines = raw.splitlines(keepends=True)
        notice = b''.join(lines[:21])
        if not notice.startswith(b'// Copyright 2024 Mozilla') or not all(x.startswith(b'//') for x in lines[:21]) or lines[20].strip() != b'// SOFTWARE.':
            raise ValueError('llamafile inline notice not recognized')
        b.add('notices/llamafile/sgemm-inline-LICENSE', notice,
              {'archive_sha256': POCKET_SHA, 'member': prefix + name, 'source_sha256': digest(raw),
               'extraction': 'entire leading copyright and license comment'}, 'llamafile')
    f = ROOT / 'runtime/native/vendor/cjson/cJSON.c'
    checked_path(f, '607e756460fa0de37d20a7a9181f2de29c97bfb7ce5a0e6c2f548243836cd852')
    raw = f.read_bytes()
    b.add('notices/cjson-native/LICENSE', raw[:raw.index(b'*/') + 2] + b'\n',
          {'source_path': str(f), 'source_sha256': digest(raw), 'extraction': 'entire leading license comment'}, 'cjson-native')
    b.local('notices/picosha2/LICENSE', ROOT / 'runtime/native/vendor/picosha2/LICENSE', 'picosha2')
    for name, raw in verified_sources().items():
        if name == 'knf/LICENSE' or name.startswith('kiss/LICENSES/') or name == 'kiss/COPYING':
            b.add('notices/' + name, raw, {'kaldi_archive_sha256': KNF_SHA, 'kiss_archive_sha256': KISS_SHA}, 'uid-frontend')
    p = ROOT / 'artifacts/sources/pocketfft-0fa0ef591e38c2758e3184c6c23e497b9f732ffa/LICENSE.md'
    raw = p.read_bytes()
    if hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() != 'c3a4c06a92d024e12ab76ce6b20eae815979552e':
        raise ValueError('pocketfft notice git blob differs')
    b.local('notices/pocketfft/LICENSE.md', p, 'native-endpoint')
    p = ROOT / 'artifacts/sources' / ('sleef-' + SLEEF_REV)
    checked_path(p / 'source.tar.gz', SLEEF_SHA)
    with tarfile.open(p / 'source.tar.gz') as t:
        raw = t.extractfile('sleef-' + SLEEF_REV + '/LICENSE.txt').read()
    b.add('notices/sleef/LICENSE.txt', raw, {'archive_sha256': SLEEF_SHA, 'revision': SLEEF_REV}, 'native-endpoint')
    for name, p, group in [
        ('voice/LICENSE', ROOT / 'LICENSE', 'first-party'),
        ('sdk/LICENSE', ROOT / '.build/aii-plugin-sdk-92a4265/LICENSE', 'sdk'),
        ('go/LICENSE', Path('/usr/local/go1.27/LICENSE'), 'go'),
        ('go/PATENTS', Path('/usr/local/go1.27/PATENTS'), 'go'),
    ]:
        b.local('notices/' + name, p, group)


def runtime_notices(b, inv):
    bindings = []
    remote = checked_path(ROOT / 'artifacts/desktop-runtime-notices-20260914-r1/notices.zip', REMOTE_SHA)
    with zipfile.ZipFile(remote) as z:
        if len(z.namelist()) != len(set(z.namelist())):
            raise ValueError('duplicate remote record')
        rm = json.loads(z.read('manifest.json'))
        if set(z.namelist()) != set(rm['files']) | {'manifest.json'} or rm['passed'] is not True:
            raise ValueError('remote collection inventory differs')
        for name, row in rm['files'].items():
            raw = z.read(name)
            if digest(raw) != row['sha256'] or len(raw) != row['bytes']:
                raise ValueError('remote notice binding differs')
            b.add('notices/' + name, raw, row['origin'], name.split('/')[0])
        b.add('evidence/remote-notice-manifest.json', z.read('manifest.json'), {'archive_sha256': REMOTE_SHA}, 'remote-binding')
        winrecord = z.read('onnxruntime-windows-1.24.4/RECORD')
        check_version(z.read('onnxruntime-windows-1.24.4/METADATA'), 'onnxruntime-directml', '1.24.4')
        for name in ['LICENSE', 'ThirdPartyNotices.txt']:
            raw = z.read('onnxruntime-windows-1.24.4/' + name)
            check_record(winrecord, 'onnxruntime/' + name, digest(raw), len(raw))
        w = inv['runtime']['windows-x86_64-native']['files']['bin/onnxruntime.dll']
        check_record(winrecord, 'onnxruntime/capi/onnxruntime.dll', w['sha256'], w['bytes'])
        bindings.append({'component': 'onnxruntime', 'platform': 'windows', 'distribution': 'onnxruntime-directml 1.24.4',
                         'source_sha256': w['sha256'], 'shipped_sha256': w['sha256'],
                         'proof': 'actual wheel RECORD plus fresh guest source hash, no relocation',
                         'execution_claim': 'CPU sessions; distribution name is not accelerator-use evidence'})
        if z.read('onnxruntime-linux-1.24.2/VERSION_NUMBER').strip() != b'1.24.2':
            raise ValueError('wrong Linux ONNX Runtime version')
    with zipfile.ZipFile(ROOT / 'deliverables/desktop-beta-native-gate-20260914-r3/linux-evidence.zip') as z:
        raw = z.read('checkpoint/freeze.json')
        f = json.loads(raw)
        b.add('evidence/linux-freeze.json', raw, {'archive': str(z.filename)}, 'runtime-binding')
    shipped = inv['runtime']['linux-x86_64-native']['files']['lib/libonnxruntime.so.1.24.2']['sha256']
    check_relocation(f['libraries']['libonnxruntime.so.1.24.2'], rm['linux_original_library_sha256'], shipped)
    bindings.append({'component': 'onnxruntime', 'platform': 'linux', 'distribution': 'onnxruntime 1.24.2',
                     'source_sha256': rm['linux_original_library_sha256'], 'shipped_sha256': shipped,
                     'proof': 'sealed upstream archive, extracted source hash and freeze relocation map'})
    p = ROOT / '.build/native-runtime-model-assets-macos-r1/python/lib/python3.11/site-packages'
    record = (p / 'onnxruntime-1.29.0.dist-info/RECORD').read_bytes()
    metadata = (p / 'onnxruntime-1.29.0.dist-info/METADATA').read_bytes()
    check_version(metadata, 'onnxruntime', '1.29.0')
    for name in ['LICENSE', 'ThirdPartyNotices.txt']:
        raw = (p / 'onnxruntime' / name).read_bytes()
        check_record(record, 'onnxruntime/' + name, digest(raw), len(raw))
        b.local('notices/onnxruntime-macos-1.29.0/' + name, p / 'onnxruntime' / name, 'onnxruntime-macos-1.29.0')
    for name in ['METADATA', 'RECORD']:
        b.local('notices/onnxruntime-macos-1.29.0/' + name, p / 'onnxruntime-1.29.0.dist-info' / name, 'onnxruntime-macos-1.29.0')
    lib = p / 'onnxruntime/capi/libonnxruntime.1.29.0.dylib'
    sha = file_hash(lib)
    check_record(record, 'onnxruntime/capi/' + lib.name, sha, lib.stat().st_size)
    fp = ROOT / 'deliverables/checkpoints/cp3-enrollment-runtime-20260913-r1/freeze.json'
    f = json.loads(fp.read_text())
    shipped = inv['runtime']['macos-arm64-native']['files']['lib/libonnxruntime.1.dylib']['sha256']
    check_relocation(f['libraries']['libonnxruntime.1.dylib'], sha, shipped)
    b.local('evidence/macos-freeze.json', fp, 'runtime-binding')
    bindings.append({'component': 'onnxruntime', 'platform': 'macos', 'distribution': 'onnxruntime 1.29.0',
                     'source_sha256': sha, 'shipped_sha256': shipped,
                     'proof': 'actual distribution RECORD, source hash and freeze relocation map'})
    wheel = checked_path(ROOT / 'artifacts/windows-torch-cpu-20260909-r1/torch-2.8.0+cpu-cp311-cp311-win_amd64.whl', TORCH_SHA)
    with zipfile.ZipFile(wheel) as z:
        info = 'torch-2.8.0+cpu.dist-info/'
        record = z.read(info + 'RECORD')
        check_version(z.read(info + 'METADATA'), 'torch', '2.8.0+cpu')
        for name in ['LICENSE', 'NOTICE', 'METADATA', 'RECORD']:
            raw = z.read(info + name)
            if name != 'RECORD':
                check_record(record, info + name, digest(raw), len(raw))
            b.add('notices/torch-windows-2.8.0+cpu/' + name, raw,
                  {'archive_sha256': TORCH_SHA, 'member': info + name}, 'torch-windows')
        version = z.read('torch/version.py')
        check_record(record, 'torch/version.py', digest(version), len(version))
        if b"git_version = 'a1cb3cc05d46d198467bebbb6e8fba50a325d4e7'" not in version:
            raise ValueError('Torch source revision differs')
        b.add('evidence/torch-version.py.txt', version,
              {'archive_sha256': TORCH_SHA, 'member': 'torch/version.py'}, 'torch-windows')
        for name in ['asmjit.dll', 'fbgemm.dll', 'c10.dll', 'torch_cpu.dll', 'libiomp5md.dll']:
            member = 'torch/lib/' + name
            with z.open(member) as f:
                sha = hashlib.file_digest(f, 'sha256').hexdigest()
            size = z.getinfo(member).file_size
            check_record(record, member, sha, size)
            w = inv['runtime']['windows-x86_64-native']['files']['bin/' + name]
            if (sha, size) != (w['sha256'], w['bytes']):
                raise ValueError('Torch wheel member differs from shipped DLL')
            bindings.append({'component': name, 'platform': 'windows', 'distribution': 'torch 2.8.0+cpu',
                             'archive_sha256': TORCH_SHA, 'member': member, 'source_sha256': sha,
                             'shipped_sha256': sha, 'bytes': size, 'proof': 'full wheel member hash and RECORD'})
    p = ROOT / 'artifacts/intel-openmp-notice-source-20260914-r2'
    checked_path(p / 'source.whl', OPENMP_SHA)
    with zipfile.ZipFile(p / 'source.whl') as z:
        info = 'intel_openmp-2025.2.0.dist-info/'
        member = 'intel_openmp-2025.2.0.data/data/Library/bin/libiomp5md.dll'
        dll = z.read(member)
        w = inv['runtime']['windows-x86_64-native']['files']['bin/libiomp5md.dll']
        if (digest(dll), len(dll)) != (w['sha256'], w['bytes']):
            raise ValueError('Intel source distribution differs from shipped DLL')
        record = z.read(info + 'RECORD')
        check_record(record, member, digest(dll), len(dll))
        check_version(z.read(info + 'METADATA'), 'intel-openmp', '2025.2.0')
        for name in ['LICENSE.txt', 'METADATA', 'RECORD']:
            raw = z.read(info + name)
            if name != 'RECORD':
                check_record(record, info + name, digest(raw), len(raw))
            b.add('notices/intel-openmp-windows-2025.2.0/' + name, raw,
                  {'archive_sha256': OPENMP_SHA, 'member': info + name}, 'intel-openmp-windows')
        third_party = 'intel_openmp-2025.2.0.data/data/share/doc/compiler/licensing/openmp/third-party-programs.txt'
        if distribution_notice_members(z.namelist()) != {info + 'LICENSE.txt', third_party}:
            raise ValueError('Intel distribution notice set changed')
        raw = z.read(third_party)
        check_record(record, third_party, digest(raw), len(raw))
        b.add('notices/intel-openmp-windows-2025.2.0/third-party-programs.txt', raw,
              {'archive_sha256': OPENMP_SHA, 'member': third_party}, 'intel-openmp-windows')
        bindings.append({'component': 'libiomp5md.dll', 'platform': 'windows',
                         'distribution': 'Intel OpenMP 2025.2.0', 'archive_sha256': OPENMP_SHA,
                         'member': member, 'source_sha256': digest(dll), 'shipped_sha256': digest(dll),
                         'proof': 'full Intel wheel member hash and RECORD, same as Torch-shipped member'})
    b.local('evidence/intel-openmp-acquisition.json', p / 'acquisition.json', 'intel-openmp-windows')
    return bindings


def validate_bundle(out):
    r = json.loads((out / 'manifest.json').read_text())
    for name, row in r['files'].items():
        p = out / name
        if p.is_symlink() or file_hash(p) != row['sha256'] or p.stat().st_size != row['bytes']:
            raise ValueError('notice bundle changed: ' + name)
    expected = set(r['files']) | {'manifest.json'}
    if {str(p.relative_to(out)) for p in out.rglob('*') if p.is_file()} != expected:
        raise ValueError('notice inventory has missing or extra files')
    groups = {v['group'] for v in r['files'].values()}
    for row in r['models']:
        if model_group(row['path']) != row['notice_group'] or row['notice_group'] not in groups:
            raise ValueError('model notice attribution missing')
    if r['public_distribution_ready'] or r['license_inventory_complete']:
        raise ValueError('collection cannot assert distribution clearance')
    return r


def validate_runtime_archive(runtime):
    """Rehash payload files and both archive-owned inventory/profile records."""
    archive = checked_path(Path(runtime['archive']['path']), runtime['archive']['sha256'])
    seen = set()
    extras = {'inventory.json': runtime['archive']['inventory_sha256'],
              'voice-runtime.json': runtime['runtime_manifest_sha256']}
    with tarfile.open(archive) as t:
        for m in t:
            if m.isdir():
                continue
            if not m.isfile():
                raise ValueError('nonregular runtime member')
            name = m.name.removeprefix('./')
            if not name.startswith('runtime/'):
                raise ValueError('runtime member is outside its root')
            name = name.removeprefix('runtime/')
            if name in seen:
                raise ValueError('duplicate runtime member')
            row = runtime['files'].get(name)
            if name not in extras and (row is None or m.size != row['bytes']):
                raise ValueError('runtime member differs: ' + name)
            with t.extractfile(m) as f:
                sha = hashlib.file_digest(f, 'sha256').hexdigest()
            expected = extras[name] if name in extras else row['sha256']
            if sha != expected:
                raise ValueError('runtime file differs: ' + name)
            seen.add(name)
    if seen != set(runtime['files']) | set(extras):
        raise ValueError('runtime inventory incomplete')


def main(out):
    invp = checked_path(ROOT / 'deliverables/desktop-release-dependencies-20260914-r1/inventory.json', INVENTORY_SHA)
    inv = json.loads(invp.read_text())
    package = ROOT / 'deliverables/checkpoints/desktop-beta-unified-20260914-r5/id.aiii.voice-0.1.0-beta.1-rc.1.aiiospkg'
    manifest, payload = read_package(package, PACKAGE_SHA)
    declared = json.loads(payload['models.json'])
    if {(x['path'], x['sha256'], x['size']) for x in declared} != {(x['path'], x['sha256'], x['size']) for x in inv['models']}:
        raise ValueError('package and inventory model sets differ')
    for runtime in inv['runtime'].values():
        validate_runtime_archive(runtime)
    b = Bundle(out)
    b.local('evidence/dependency-inventory.json', invp, 'candidate-binding')
    b.local('evidence/collector.py', Path(__file__), 'collector-source')
    b.local('evidence/remote-collector.py', ROOT / 'scripts/collect_remote_voice_notices.py', 'collector-source')
    static_notices(b)
    bindings = runtime_notices(b, inv)
    model_notices(b)
    report = {'collection_passed': True, 'observed_utc': datetime.now(timezone.utc).isoformat(),
              'candidate_sha256': PACKAGE_SHA, 'dependency_inventory_sha256': INVENTORY_SHA,
              'runtime_distribution_bindings': bindings, 'files': b.files,
              'models': [{'path': x['path'], 'sha256': x['sha256'], 'bytes': x['size'], 'platforms': x['platforms'],
                          'notice_group': model_group(x['path'])} for x in inv['models']],
              'coverage': 'exact distribution-level notices plus conservatively included native source notices; not an exhaustive linked-object SBOM',
              'open_items': [
                  'WeSpeaker delegates model licensing to training datasets; VoxBlink2 explicitly calls its model CC-BY-NC-SA-4.0. Exact combined UID release disposition is not established by an Apache software license.',
                  'The byte-matched Intel OpenMP 2025.2.0 distribution carries its Developer Tools EULA (August 2024), not a guessed generic open-source license. Its redistribution conditions require review for the final packaging; EULA and all named third-party notices are retained.',
                  'Silero and Smart Turn model cards declare licenses; corresponding upstream notices are retained, but the exporters do not bind these converted files to a precise software commit.',
                  'AsmJit license is from the Torch git-version -> FBGEMM -> AsmJit submodule chain; wheel-to-DLL identity is exact, but a bit-reproduced source build is not claimed.',
                  'Final release must incorporate notices and real asset URLs before freezing/signing; this evidence bundle did not repack the working candidate.',
              ], 'license_inventory_complete': False, 'public_distribution_ready': False,
              'package_changed': False, 'signed': False, 'installed': False, 'uploaded': False,
              'model_or_engine_launched': False}
    with (out / 'manifest.json').open('x') as f:
        json.dump(report, f, indent=2)
        f.write('\n')
    validate_bundle(out)
    if file_hash(package) != PACKAGE_SHA:
        raise ValueError('candidate changed during notice collection')
    print(json.dumps({'collection_passed': True, 'files': len(b.files), 'model_files': len(report['models']),
                      'runtime_distribution_bindings': len(bindings), 'manifest_sha256': file_hash(out / 'manifest.json'),
                      'public_distribution_ready': False}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path)
    p.add_argument('--verify', type=Path)
    args = p.parse_args()
    if bool(args.out) == bool(args.verify):
        p.error('choose --out or --verify')
    if args.verify:
        r = validate_bundle(args.verify)
        print(json.dumps({'verified': True, 'files': len(r['files'])}))
    else:
        main(args.out)
