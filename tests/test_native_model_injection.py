from pathlib import Path
import os
import subprocess

import pytest
from scripts import native_probe

ROOT=Path(__file__).resolve().parents[1]
SESSION=ROOT/'runtime/native/session'


@pytest.mark.parametrize('mutation',[None,'construct_default','leak_owner'])
def test_injected_model_factory_and_compiling_falsifiers(tmp_path,mutation):
    source=(SESSION/'native_models.cpp').read_text();wanted=None
    if mutation=='construct_default':
        before='hearing(supplied?RecognizerFactory{}:shared_hearing(p)),\n       asr(supplied?std::move(supplied):hearing())'
        after='hearing(shared_hearing(p)),\n       asr((supplied.reset(),hearing()))'
        wanted='default ASR must be explicit'
    elif mutation=='leak_owner':
        before='NativeModels::~NativeModels()=default;'
        after='NativeModels::~NativeModels(){(void)p_.release();}'
        wanted='component ownership leak'
    if mutation:
        assert source.count(before)==1;source=source.replace(before,after)
    candidate=tmp_path/'native_models.cpp';candidate.write_text(source)
    channel=[SESSION/name for name in ('model_channel.cpp','model_link.cpp','model_server_control.cpp','admission.cpp')]
    sources=[candidate,*[SESSION/name for name in ('native_models_injection_test.cpp','native_c_api.cpp','session.cpp','text.cpp','c_api.cpp')]]
    strict,plain=(sources,channel) if os.name=='nt' else (sources+channel,[])
    probe=native_probe.build(tmp_path,'probe',strict,plain_sources=plain,includes=[SESSION],threads=True,
                             defines=['AII_VAD_BUILD','AII_ENDPOINT_BUILD'],timeout=90).path
    run=subprocess.run([str(probe),str(tmp_path)],capture_output=True,text=True,timeout=20)
    (tmp_path/'stdout').write_text(run.stdout);(tmp_path/'stderr').write_text(run.stderr)
    if wanted:assert run.returncode!=0 and wanted in run.stderr,(run.stdout,run.stderr)
    else:assert run.returncode==0,run.stderr
