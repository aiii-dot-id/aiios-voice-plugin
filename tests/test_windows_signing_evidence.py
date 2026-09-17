"""Rebinding must neither launder a changed executable nor claim signed readiness."""
import copy
import hashlib
import json
import struct
from pathlib import Path
import pytest

from scripts.rebind_signed_windows_runtime import OWNED, SUBJECT, signing_only_change, validate_report, verify_authenticode



def test_signer_and_rebinder_have_identical_targets():
    import re
    script=(Path(__file__).resolve().parents[1]/'scripts/sign_windows_native_runtime.ps1').read_text()
    block=script.split('$owned=@(',1)[1].split(')',1)[0]
    assert set(re.findall(r"'([^']+)'",block))==OWNED


def test_actual_unsigned_packaged_pes_have_expected_layout():
    # Production input readback, not a claim that fabricated certificates verify.
    root=Path(__file__).resolve().parents[1]
    base=root/'deliverables/beta1-unsigned-windows-companion-20260916-r1/companion-tree'
    assert base.exists(), 'current unsigned runtime must be present, never skip this gate'
    profile=json.loads((base/'voice-runtime.json').read_text())
    for name in OWNED:
        raw=(base/name).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==profile['files'][name]['sha256']
        pe=struct.unpack_from('<I',raw,60)[0];optional=pe+24
        magic=struct.unpack_from('<H',raw,optional)[0]
        directory=optional+(96 if magic==0x10b else 112)+32
        fake=bytearray(raw);padding=(-len(fake))%8;fake.extend(b'\0'*padding)
        offset=len(fake);fake.extend(struct.pack('<IHH',16,0x200,2)+b'FIXTURE!')
        struct.pack_into('<II',fake,directory,offset,16)
        signing_only_change(raw,fake)
