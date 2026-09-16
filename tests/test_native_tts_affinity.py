import copy
import subprocess
import pytest
from scripts.native_tts_affinity_contract import ORDER,PINS,parse_pins,validate_permit,with_restored_pins,decode_file_open
from scripts.native_tts_affinity_contract import write_state
from scripts.audit_native_tts_affinity import assess
from scripts.audit_native_tts_affinity import placement
from scripts.audit_native_precomputed_windows import digest
from tests.test_native_tts_priority import fixture


def test_missing_state_file_is_wait_not_a_controller_fault():
    exact="error: guest agent command failed: unable to execute QEMU agent command 'guest-file-open': failed to open file 'C:/work/proof/run/coordination.json': The system cannot find the file specified.\n"
    assert decode_file_open(subprocess.CompletedProcess(['virsh'],1,'',exact)) is None
    assert decode_file_open(subprocess.CompletedProcess(['virsh'],0,'{"return": 12}',''))==12
    for error in ('Access denied.','guest agent not connected',exact.replace('guest-file-open','guest-file-read')):
        with pytest.raises(subprocess.CalledProcessError):decode_file_open(subprocess.CompletedProcess(['virsh'],1,'',error))


def test_status_publication_never_replaces_a_reader_held_message(tmp_path,monkeypatch):
    import os
    replace=os.replace
    def no_delete_sharing(src,dst):
        if dst.exists():raise PermissionError('Windows reader denies FILE_SHARE_DELETE')
        replace(src,dst)
    monkeypatch.setattr(os,'replace',no_delete_sharing)
    for i in (1,2):
        for state in ('waiting','running'):write_state(tmp_path,{'state':state,'request':{'index':i}})
    write_state(tmp_path,{'state':'complete','passed':True})
    assert len(list(tmp_path.glob('coordination-*.json')))==5
    with pytest.raises(AssertionError,match='repeated'):write_state(tmp_path,{'state':'complete','passed':True})


def test_exact_order_balances_position():
    assert len(ORDER)==8
    assert sum(i for i,a in enumerate(ORDER) if a=='local')==sum(i for i,a in enumerate(ORDER) if a=='unrestricted')
    assert all(set(ORDER[i:i+2])==set(PINS) for i in range(0,8,2))


@pytest.mark.parametrize('damage',['nonce','index','arm','pins','persistent','extra'])
def test_refuse_wrong_placement_admission(damage):
    request={'index':1,'arm':'local','nonce':'bound'}
    permit={'request':copy.deepcopy(request),'live_pins':PINS['local'],'persistent_unchanged':True}
    validate_permit(permit,request)
    if damage in ('nonce','index','arm'):permit['request'][damage]='wrong'
    elif damage=='pins':permit['live_pins']=PINS['unrestricted']
    elif damage=='persistent':permit['persistent_unchanged']=False
    else:permit['extra']=True
    with pytest.raises(AssertionError):validate_permit(permit,request)


@pytest.mark.parametrize('fail_at',[None,0,3,7])
def test_partial_or_complete_placement_is_restored(fail_at):
    state=PINS['unrestricted'].copy();calls=[]
    def read():return state.copy()
    def set_pin(i,p):state[i]=p;calls.append((i,p))
    def operation(prior):
        for i,p in enumerate(PINS['local']):
            set_pin(i,p)
            if i==fail_at:raise RuntimeError('injected placement error')
    if fail_at is None:with_restored_pins(read,set_pin,operation)
    else:
        with pytest.raises(RuntimeError,match='injected'):with_restored_pins(read,set_pin,operation)
    assert state==PINS['unrestricted'] and calls[-8:]==list(enumerate(PINS['unrestricted']))


def test_failed_restoration_reports_failure_and_tries_remaining_cpus():
    state=PINS['unrestricted'].copy();calls=[]
    def read():return state.copy()
    def set_pin(i,p):
        calls.append(i)
        if i==3:raise OSError('refused')
        state[i]=p
    def operation(prior):state[:]=PINS['local']
    with pytest.raises(RuntimeError,match='restoration failed'):with_restored_pins(read,set_pin,operation)
    assert calls==list(range(8)) and state[3]=='3'


def test_pin_parser_refuses_missing_or_duplicate_vcpu():
    good=' VCPU CPU Affinity\n---\n'+''.join(f'{i} 0-31\n' for i in range(8))
    assert parse_pins(good)==PINS['unrestricted']
    with pytest.raises(AssertionError):parse_pins(good.replace('7 0-31\n',''))
    with pytest.raises(AssertionError):parse_pins(good+'0 0-31\n')


def comparison():
    runs=fixture()
    for run,arm in zip(runs,ORDER):
        run['arm']=arm
        for i in (0,1,2,4):run['rows'][i]['priority_class']=32
        if arm=='local':
            for i in (1,2,4):
                run['rows'][i]['first_pcm_seconds']*=.8
                run['rows'][i]['seconds']*=.9
    return runs


@pytest.mark.parametrize('damage',[None,'recovery','load','memory','one_pair','missing'])
def test_frozen_performance_does_not_average_away_a_regression(damage):
    runs=comparison();assert assess(runs)['performance_gate_passed']
    if damage is None:return
    if damage=='missing':
        runs.pop()
        with pytest.raises(AssertionError):assess(runs)
        return
    for run in runs:
        if run['arm']!='local':continue
        if damage=='recovery':run['rows'][4]['seconds']*=1.3
        if damage=='load':run['rows'][0]['seconds']*=1.1
        if damage=='memory':run['rows'][4]['resources']['peak_working_bytes']*=2
    if damage=='one_pair':runs[1]['rows'][1]['first_pcm_seconds']/=.7
    assert not assess(runs)['performance_gate_passed']


@pytest.mark.parametrize('damage',[None,'restored_flag','restored_pins','persistent','nonce','placement','controller','missing'])
def test_independent_audit_requires_actual_placement_and_restoration(damage):
    class Source:
        def read(self,path):
            assert path=='host/control_native_tts_affinity.py'
            return b'fixed controller'
    permits=[]
    for i,arm in enumerate(ORDER,1):
        permits.append({'request':{'index':i,'arm':arm,'nonce':f'{i:032x}'},
                        'live_pins':PINS[arm].copy(),'persistent_unchanged':True})
    pins='\n'.join(f'{i} 0-31' for i in range(8))
    host={'passed':True,'restored':True,'controller_sha256':digest(b'fixed controller'),
          'prior_pins':PINS['unrestricted'].copy(),'after_pins':PINS['unrestricted'].copy(),
          'persistent_before':pins,'persistent_after':pins,
          'cache_sizes':['98304K','32768K'],'cache_members':['0-7,16-23','8-15,24-31'],
          'events':[{'observed_at_utc':'test',**copy.deepcopy(p)} for p in permits]}
    r={'affinity_admissions':permits};placement(r,host,Source())
    if damage is None:return
    if damage=='restored_flag':host['restored']=False
    elif damage=='restored_pins':host['after_pins'][2]='2'
    elif damage=='persistent':host['persistent_after']='changed'
    elif damage=='nonce':host['events'][1]['request']['nonce']=permits[0]['request']['nonce']
    elif damage=='placement':host['events'][1]['live_pins']=PINS['unrestricted']
    elif damage=='controller':host['controller_sha256']=digest(b'other controller')
    elif damage=='missing':host['events'].pop()
    with pytest.raises(AssertionError):placement(r,host,Source())
