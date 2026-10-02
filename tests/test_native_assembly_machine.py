"""Family assembly must describe a native, byte-bound carrier on each desktop."""
from pathlib import Path
import pytest
from scripts.assemble_guided_beta_candidate import native_contract_carrier

def bindings():
    return {
        'macos-arm64-full':dict(platform='macos',arch='arm64',carrier=Path('mac-carrier')),
        'linux-x86_64-full':dict(platform='linux',arch='x86_64',carrier=Path('linux-carrier')),
        'windows-x86_64-full':dict(platform='windows',arch='x86_64',carrier=Path('windows-carrier')),
        'linux-x86_64-small':dict(platform='linux',arch='x86_64',carrier=Path('linux-small')),
    }

@pytest.mark.parametrize('system,machine,expected',[
    ('Darwin','arm64','mac-carrier'),('Darwin','aarch64','mac-carrier'),
    ('Linux','x86_64','linux-carrier'),('Windows','AMD64','windows-carrier'),
])
def test_selects_actual_native_carrier(system,machine,expected):
    assert native_contract_carrier(bindings(),system=system,machine=machine)==Path(expected)

@pytest.mark.parametrize('system,machine',[
    ('FreeBSD','x86_64'),('Linux','arm64'),('Darwin','x86_64'),('Windows','unknown'),
])
def test_foreign_or_unknown_machine_refused(system,machine):
    with pytest.raises(ValueError,match='executed natively'):
        native_contract_carrier(bindings(),system=system,machine=machine)

def test_missing_native_payload_cannot_borrow_other_desktop():
    rows=bindings();del rows['windows-x86_64-full']
    with pytest.raises(ValueError,match='executed natively'):
        native_contract_carrier(rows,system='Windows',machine='AMD64')
