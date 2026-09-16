import copy
import pytest
from scripts.stage_native_prepared_session import ROOT,runner
from scripts.audit_native_prepared_session import profiles,audit_program

def test_full_conversations_and_timing_predicates_unchanged():
    original=(ROOT/'scripts/compare_native_overlap_load_windows.py').read_bytes()
    result=runner(original).decode()
    anchor="        for arm,profile in r['profiles'].items():"
    assert result[result.index(anchor):]==original.decode()[original.decode().index(anchor):]
    assert "component_audit['measured']['performance_gate_passed']" in result
    assert "if arm=='candidate':profile['model_paths'][0]" in result

@pytest.mark.parametrize('damage',[None,'tts','words','uid','asr-path'])
def test_only_recognizer_artifact_can_change(damage):
    a={'worker':'old','libraries':{'aii_native_asr.dll':'a','tts':'t'},'model_paths':['old','same'],'backend':'vulkan','uid_model':'u'}
    b=copy.deepcopy(a);b['worker']='new';b['libraries']['aii_native_asr.dll']='b';b['model_paths'][0]='prepared'
    if damage=='tts':b['libraries']['tts']='changed'
    if damage=='words':b['model_paths'][1]='changed'
    if damage=='uid':b['uid_model']='changed'
    if damage=='asr-path':b['model_paths'][0]='old'
    if damage:
        with pytest.raises(AssertionError):profiles(a,b)
    else:profiles(a,b)

def test_raw_audio_and_barge_audit_not_replaced_by_presence_checks():
    old=(ROOT/'scripts/audit_native_overlap_load.py').read_bytes()
    new=audit_program(old)
    start='    golden={};rows=[];barges=[]'
    # Threshold dictionary spelling is the only mechanical change in this
    # suffix; every raw PCM, cancel/recovery and retirement check stays intact.
    expected=old.decode().replace("contract['tts_every_position_ratio_max']","contract['every_tts_position_ratio_max']")
    assert new[new.index(start):]==expected[expected.index(start):]
    compile(new,'prepared_audit','exec')
