"""Fetch the upstream notices of the models a desktop release ships.

This is an attribution bundle, not a license compatibility judgment. A notice
fetched at a pinned revision is bound to its digest; a page that can change is
recognized by a marker. No weights, models or engines are loaded or downloaded.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePosixPath
import urllib.request


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_hash(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


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

    def public(self, name, url, group, marker, bound=1024 * 1024, sha256=None):
        """A fetched notice is bound by its pinned digest when one is given; the
        marker alone only recognizes the text, as it did before a pin existed."""
        req = urllib.request.Request(url, headers={'User-Agent': 'aiios-voice-plugin-notice-audit/1'})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read(bound + 1)
            if r.status != 200 or len(raw) > bound:
                raise ValueError('notice download failed or exceeded bound: ' + name)
        if sha256 is not None and digest(raw) != sha256:
            raise ValueError('notice content differs from its pinned digest: ' + name)
        if marker not in raw.decode('utf-8'):
            raise ValueError('notice content not recognized: ' + name)
        origin = {'url': url, 'retrieved_utc': datetime.now(timezone.utc).isoformat(),
                  'source_kind': 'public text, content sealed at retrieval'}
        if sha256 is not None:
            origin['pinned_sha256'] = sha256
        return self.add(name, raw, origin, group)


def model_notices(b):
    hf = 'https://huggingface.co/'
    gh = 'https://raw.githubusercontent.com/'
    # A notice fetched at a pinned revision is bound to the digest the released
    # package records for it; a page that can change is recognized by its marker.
    sources = [
        ('nemotron-asr/README.md', hf + 'nvidia/nemotron-3.5-asr-streaming-0.6b/raw/1c8deaecc64b91f034d73e08dd8b64625eb3395d/README.md', 'nemotron-asr', 'openmdw',
         'a3344caadf796c084c6b90a9fa5978068fd45e3a019790bebe50489bb3c0f7b7'),
        ('pocket-tts/README.md', hf + 'kyutai/pocket-tts-without-voice-cloning/raw/d29db7978e464fb90cb3359ee0c69a273b9142cc/README.md', 'pocket-tts', 'cc-by-4.0',
         'ae2ebac6f8039d761ca90e2b742136dce9d7872ec8dd2105e3b2de1e3021e3aa'),
        ('pocket-voice-embeddings/README.md', hf + 'kyutai/pocket-tts-without-voice-cloning/raw/e81d79e8194ad4c7ce879c87a4258ef20cbf2487/README.md', 'pocket-voice-embeddings', 'cc-by-4.0',
         'ae2ebac6f8039d761ca90e2b742136dce9d7872ec8dd2105e3b2de1e3021e3aa'),
        ('pocket-voice-embeddings/reference-sources.md', hf + 'kyutai/tts-voices/raw/323332d33f997de8394f24a193e1a76df720e01a/README.md', 'pocket-voice-embeddings', 'Alba',
         '47c610f38e0e0bfb4b353a2afa84a5277b748b1b808d5f77fb8ef74fb836689b'),
        ('pocket-config/LICENSE', gh + 'kyutai-labs/pocket-tts/896e934690afc0e1047a3667a13514386c1420fc/LICENSE', 'pocket-config', 'Permission',
         '23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3'),
        ('silero-vad/README.md', hf + 'onnx-community/silero-vad/raw/e71cae966052b992a7eca6b17738916ce0eca4ec/README.md', 'silero-vad', 'license: mit',
         '394ac7912169c60fa95cc6c33c615f78368749f0f305b6cb43058a28233cf00a'),
        ('silero-vad/upstream-LICENSE', gh + 'snakers4/silero-vad/caddb3b7ce1dee88a14d5621a0e9a8fdeb2c2c48/LICENSE', 'silero-vad', 'Silero Team',
         '2e63e9a38b6e8fc0c7bc37ce174caca1862870856c6daf5697cfb785e925520b'),
        ('smart-turn/README.md', hf + 'pipecat-ai/smart-turn-v3/raw/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/README.md', 'smart-turn', 'bsd-2-clause',
         '8b87ad1bb42432c1c8221944a7de5a0d1a52c2162af6f447ee9522530b38f199'),
        ('smart-turn/upstream-LICENSE', gh + 'pipecat-ai/smart-turn/24c720337e17befe0413bbc93b3504036c3a3bdc/LICENSE', 'smart-turn', 'Daily',
         '0d66364067f678c08586ebb60a16a2aed4fa081ec11057df35585759ce0e774f'),
        ('wespeaker-uid/catalog.md', gh + 'wenet-e2e/wespeaker/dfa741957e5c11f477623b6e583d67d0af25ee88/docs/pretrained.md', 'wespeaker-uid', 'VoxBlink',
         '34a46fc9faeb6a5c8204c840a06b1ca94fc5ac3c52d5460f5bd6c1bf9aa701cf'),
        ('wespeaker-uid/voxblink2-LICENSE', gh + 'VoxBlink2/ScriptsForVoxBlink2/50846d6540783824476e16c006f4a0fe27e70683/LICENSE', 'wespeaker-uid', 'CC BY-NC-SA 4.0',
         'c6c9e95e3971304341fd900d62334eaa0418644b630a351a35c839ab1caf1e23'),
        ('wespeaker-uid/voxblink2-model-terms.html', 'https://voxblink2.github.io/', 'wespeaker-uid', 'license of the model is also', None),
        ('wespeaker-uid/voxceleb-terms.html', 'https://mm.kaist.ac.kr/datasets/voxceleb/', 'wespeaker-uid', 'Attribution', None),
        ('terms/CC-BY-4.0.txt', 'https://creativecommons.org/licenses/by/4.0/legalcode.txt', 'common-terms', 'Attribution 4.0', None),
        ('terms/CC-BY-NC-SA-4.0.txt', 'https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.txt', 'common-terms', 'NonCommercial', None),
        ('terms/CC0-1.0.txt', 'https://creativecommons.org/publicdomain/zero/1.0/legalcode.txt', 'common-terms', 'CC0 1.0', None),
        ('terms/OpenMDW-1.1.html', 'https://openmdw.ai/license/1-1/', 'nemotron-asr', 'OpenMDW', None),
        ('asmjit/LICENSE.md', gh + 'asmjit/asmjit/e5d7c0bd5d9aec44d68830187138149e6a8c4e32/LICENSE.md', 'asmjit', 'Copyright',
         'c8d30b463d35bd5a14b868bae5d8345a1a77cbc6b4a94669f437c619f2ebef61'),
    ]
    for name, url, group, marker, sha256 in sources:
        b.public('notices/' + name, url, group, marker, sha256=sha256)
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
