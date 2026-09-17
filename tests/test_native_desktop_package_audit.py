import copy
import io
import struct
import tarfile

import pytest

from scripts.audit_native_desktop_packages import (
    assert_binary_target, carrier_refresh_evidence, digest, memory_evidence, tar_files,
)
from tests.conftest import skip_unless_shipped


@pytest.mark.parametrize('damage',[None,'parent','runtime','model','model-root','library','settings','unchanged-carrier'])
def test_carrier_only_claim_is_bound_to_the_exact_parent(damage):
    raw=b'parent freeze';settings=b'actual settings bytes';new_settings=settings
    parent={'runtime_manifest_sha256':'runtime','models':{'weights':'hash'},
            'models_root':'/retained/models','library_hashes':{'engine':'bytes'},
            'carrier_sha256':'old-carrier'}
    fresh=copy.deepcopy(parent)
    fresh.update(parent_freeze_sha256=digest(raw),carrier_sha256='new-carrier',settings_sha256=digest(settings))
    profile={'files':{'engine':{'sha256':'bytes'}}};new_profile=copy.deepcopy(profile)
    if damage=='parent':fresh['parent_freeze_sha256']='other'
    elif damage=='runtime':new_profile['files']['extra']={}
    elif damage=='model':fresh['models']['weights']='other'
    elif damage=='model-root':fresh['models_root']='/other/models'
    elif damage=='library':fresh['library_hashes']['engine']='other'
    elif damage=='settings':new_settings=b'changed';fresh['settings_sha256']=digest(new_settings)
    elif damage=='unchanged-carrier':fresh['carrier_sha256']=parent['carrier_sha256']
    if damage:
        with pytest.raises(AssertionError):carrier_refresh_evidence(fresh,parent,raw,new_profile,profile,new_settings,settings)
    else:carrier_refresh_evidence(fresh,parent,raw,new_profile,profile,new_settings,settings)


def memory_fixture():
    raw=b'bound freeze';frozen={'carrier_sha256':'carrier'}
    speech={'checkpoint':{'root':'/cp'},'loaded_worker':{'pid':42,'parent_pid':41}}
    rows=[{'pid':41,'created':100,'exe':'/cp/runtime/aii-voice-t3',
           'observed_exe':'/cp/runtime/aii-voice-t3','rss':10},
          {'pid':42,'created':101,'exe':'/cp/runtime/bin/aii_voice_worker',
           'observed_exe':'/cp/runtime/bin/aii_voice_worker','rss':100}]
    samples=[{'elapsed':0,'observed_monotonic_ns':100,'processes':[]},
             {'elapsed':.05,'observed_monotonic_ns':50000100,'processes':rows},
             {'elapsed':.1,'observed_monotonic_ns':100000100,'processes':copy.deepcopy(rows)}]
    memory={'passed':True,'exit_code':0,'survivors':[],'freeze_sha256':digest(raw),
            'carrier_sha256':'carrier','owner_pid':40,'samples':samples,'sample_count':3,
            'sampled_peak_sum_rss_bytes':110,'max_sample_gap_seconds':.05}
    return memory,speech,frozen,raw,{40,41,42}


def test_memory_peak_recomputed_from_exact_owner_samples():
    r=memory_evidence(*memory_fixture())
    assert r['sampled_peak_sum_rss_bytes']==110
    assert not r['worst_case_memory_qualified'] and not r['gpu_memory_measured']

@pytest.mark.parametrize('damage',[None,'worker','overlap','late_birth','foreign_parent','unretired','undeclared','reused'])
def test_startup_carrier_is_accounted_without_admitting_foreign_or_concurrent_owners(damage):
    skip_unless_shipped('scripts.measure_common_native_checkpoint')
    from scripts.measure_common_native_checkpoint import classify_owners
    args=memory_fixture();memory=args[0];loaded=args[1]['loaded_worker']
    startup={'pid':39,'created':99,'parent_pid':40,'exe':'/cp/runtime/aii-voice-t3',
             'observed_exe':'/cp/runtime/aii-voice-t3','rss':250}
    memory['samples'][0]['processes']=[startup]
    memory['sampled_peak_sum_rss_bytes']=250
    memory['pre_resident_carrier_pids']=[39];args[-1].add(39)
    assert classify_owners(memory['samples'],loaded,40)==[39]
    assert memory_evidence(*args)['sampled_peak_sum_rss_bytes']==250
    if damage is None:return
    if damage=='worker':startup.update(exe='/cp/runtime/bin/aii_voice_worker',observed_exe='/cp/runtime/bin/aii_voice_worker')
    elif damage=='overlap':memory['samples'][2]['processes'].append(copy.deepcopy(startup))
    elif damage=='late_birth':startup['created']=102
    elif damage=='foreign_parent':startup['parent_pid']=900
    elif damage=='unretired':args[-1].remove(39)
    elif damage=='undeclared':memory['pre_resident_carrier_pids']=[]
    elif damage=='reused':
        changed=copy.deepcopy(startup);changed['created']=99.1
        memory['samples'][1]['processes'].append(changed)
    with pytest.raises(AssertionError):memory_evidence(*args)
    if damage not in ('unretired','undeclared'):
        with pytest.raises(AssertionError):classify_owners(memory['samples'],loaded,40)


@pytest.mark.parametrize('damage',['peak','count','gap','birth','exe','observed_exe','pid',
                                 'duplicate','retired','survivor','clock','freeze','carrier'])
def test_memory_false_closure_is_refused(damage):
    args=memory_fixture();memory=args[0];row=memory['samples'][2]['processes'][1]
    if damage=='peak':memory['sampled_peak_sum_rss_bytes']=109
    elif damage=='count':memory['sample_count']=99
    elif damage=='gap':memory['max_sample_gap_seconds']=.02
    elif damage=='birth':row['created']=999
    elif damage in ('exe','observed_exe'):row[damage]='/other/engine'
    elif damage=='pid':row['pid']=99
    elif damage=='duplicate':memory['samples'][2]['processes'].append(copy.deepcopy(row))
    elif damage=='retired':args[-1].remove(42)
    elif damage=='survivor':memory['survivors']=[42]
    elif damage=='clock':memory['samples'][2]['observed_monotonic_ns']=1
    elif damage=='freeze':memory['freeze_sha256']='other'
    else:memory['carrier_sha256']='other'
    with pytest.raises(AssertionError):memory_evidence(*args)


def test_binary_target_is_read_from_bytes_not_a_platform_label():
    elf=bytearray(64);elf[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',elf,18,62)
    pe=bytearray(80);pe[:2]=b'MZ';struct.pack_into('<I',pe,0x3c,64)
    pe[64:68]=b'PE\0\0';struct.pack_into('<H',pe,68,0x8664)
    assert_binary_target(elf,'linux');assert_binary_target(pe,'windows')
    with pytest.raises(AssertionError):assert_binary_target(pe,'linux')
    with pytest.raises(AssertionError):assert_binary_target(elf,'windows')
    struct.pack_into('<H',elf,18,183)
    with pytest.raises(AssertionError):assert_binary_target(elf,'linux')


@pytest.mark.parametrize('damage',['traversal','symlink','duplicate'])
def test_tar_rejects_unsafe_or_ambiguous_members(tmp_path,damage):
    path=tmp_path/'bad.tar'
    with tarfile.open(path,'w') as archive:
        name='../escape' if damage=='traversal' else 'file'
        member=tarfile.TarInfo(name);member.size=1
        if damage=='symlink':member.type=tarfile.SYMTYPE;member.linkname='/elsewhere';member.size=0
        archive.addfile(member,io.BytesIO(b'x'))
        if damage=='duplicate':archive.addfile(member,io.BytesIO(b'x'))
    with pytest.raises(AssertionError):tar_files(path)
