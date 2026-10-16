"""The Windows images the release signs: every release-built PE image.

The census comes from the runtime's own inventory and the ownership rule the
release gates apply (release_owned_image), so an image this release builds
cannot ship unsigned and the signer, the rebind and staging cannot disagree.
The carrier is excluded here because it is rebuilt after the rebind and signed
in its own step. Run by the PowerShell signer: prints one inventory path per line.
"""
import argparse
import json
from pathlib import Path

from scripts.check_native_binary_privacy import release_owned_image

CARRIER = 'aii-voice-t3.exe'
# A floor, not the census: every Windows runtime ships these, so a broken
# ownership rule cannot shrink the signed set to a subset of them.
CORE_IMAGES = frozenset(('bin/aiii_uid_frontend.dll', 'bin/aii_native_asr.dll',
    'bin/aii_native_endpoint.dll', 'bin/aii_native_uid.dll', 'bin/aii_native_vad.dll',
    'bin/aii_voice_runtime.dll', 'bin/aii_voice_worker.exe', 'bin/native_pocket_resident.dll'))


def signing_targets(files):
    targets = frozenset(name for name in files if name != CARRIER
                        and name.lower().endswith(('.dll', '.exe')) and release_owned_image(name))
    if not CORE_IMAGES <= targets:
        raise ValueError('Windows owned-image inventory incomplete')
    return targets


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    profile = json.loads((parser.parse_args().runtime/'voice-runtime.json').read_text(encoding='utf-8-sig'))
    print('\n'.join(sorted(signing_targets(profile['files']))))
