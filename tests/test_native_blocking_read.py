import copy
from pathlib import Path

import pytest

from scripts.audit_native_blocking_read import gates, pipe_cases


def rows():
    return [{'arm':arm,'speech':[{'first_pcm_ms':100,'rtf':.5} for _ in range(4)],
             'thread_windows':[{'name':n,'summary':{'process_mean_cores':.4 if arm=='baseline' else .12}}
                               for n in ('before_sessions','after_session_0','after_session_1')],
             'readiness':{'host_startup_timing':{'spawn_to_ready_seconds':40}}}
            for arm in ('baseline','candidate','candidate','baseline')]


def test_idle_gain_cannot_buy_a_speech_or_startup_regression():
    r=rows();assert gates(r)['passed']
    for key in ('first_pcm_ms','rtf'):
        bad=copy.deepcopy(r);bad[1]['speech'][0][key]*=1.11
        assert not gates(bad)['passed']
    bad=copy.deepcopy(r);bad[1]['readiness']['host_startup_timing']['spawn_to_ready_seconds']*=1.11
    assert not gates(bad)['passed']
    bad=copy.deepcopy(r);bad[1]['thread_windows'][2]['summary']['process_mean_cores']=.4
    assert not gates(bad)['passed']


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1])
def test_nonfinite_or_negative_counters_refuse(value):
    r=rows();r[1]['thread_windows'][0]['summary']['process_mean_cores']=value
    with pytest.raises(AssertionError):gates(r)


def test_native_read_cancellation_remains_in_the_retirement_watchdog():
    root=Path(__file__).resolve().parents[1]
    body=(root/'runtime/native/session/worker.cpp').read_text().split('while (live_threads_) {',1)[1].split('for (auto &t : threads_)',1)[0]
    assert 'controls_.interrupt();' in body and 'input_.interrupt();' in body
    pipe=(root/'runtime/native/session/worker_io.cpp').read_text()
    assert 'PeekNamedPipe' not in pipe
    cmake=(root/'runtime/native/session/CMakeLists.txt').read_text()
    assert 'target_compile_definitions(aii_worker_io_test PRIVATE AII_PIPE_TEST_HOOK)' in cmake
    assert 'aii_voice_worker PRIVATE AII_PIPE_TEST_HOOK' not in cmake


@pytest.mark.parametrize('damage',['none','missing','skipped','failed','duplicate'])
def test_zero_exit_is_not_proof_that_the_named_pipe_tests_executed(damage):
    names=['native_worker_pipe_'+n for n in ('contracts','deadline','idle','cancel_race')]
    if damage=='missing':names.pop()
    if damage=='duplicate':names[-1]=names[0]
    tag='<failure/>' if damage=='failed' else '<skipped/>' if damage=='skipped' else ''
    raw='<testsuite failures="0" disabled="0" skipped="0">'+''.join(f'<testcase name="{n}" status="run">{tag}</testcase>' for n in names)+'</testsuite>'
    if damage=='none':assert pipe_cases(raw)==sorted(names)
    else:
        with pytest.raises(AssertionError):pipe_cases(raw)
