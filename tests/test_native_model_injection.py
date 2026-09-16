"""Private platform injection must replace construction, preserve ownership."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]
SESSION=ROOT/'runtime/native/session'


@pytest.mark.parametrize('mutation',[None,'construct_default','leak_owner'])
def test_injected_model_factory_and_compiling_falsifiers(tmp_path,mutation):
    source=(SESSION/'native_models.cpp').read_text();wanted=None
    if mutation=='construct_default':
        before='asr(supplied?std::move(supplied):std::make_unique<Asr>(p))'
        after='asr((supplied.reset(),std::make_unique<Asr>(p)))'
        wanted='default ASR must be explicit'
    elif mutation=='leak_owner':
        before='NativeModels::~NativeModels()=default;'
        after='NativeModels::~NativeModels(){(void)p_.release();}'
        wanted='component ownership leak'
    if mutation:
        assert source.count(before)==1;source=source.replace(before,after)
    candidate=tmp_path/'native_models.cpp';candidate.write_text(source)
    compiler=shutil.which('clang++') or shutil.which('g++');assert compiler
    command=[compiler,'-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread','-I'+str(SESSION),str(candidate),
             *[str(SESSION/name) for name in ('native_models_injection_test.cpp','native_c_api.cpp','session.cpp','text.cpp','c_api.cpp')],
             '-o',str(tmp_path/'probe')]
    built=subprocess.run(command,capture_output=True,text=True,timeout=90)
    assert built.returncode==0,built.stderr
    run=subprocess.run([str(tmp_path/'probe'),str(tmp_path)],capture_output=True,text=True,timeout=20)
    (tmp_path/'stdout').write_text(run.stdout);(tmp_path/'stderr').write_text(run.stderr)
    if wanted:assert run.returncode!=0 and wanted in run.stderr,(run.stdout,run.stderr)
    else:assert run.returncode==0,run.stderr
