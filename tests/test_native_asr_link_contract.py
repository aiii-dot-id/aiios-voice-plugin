"""Real configure/link falsifiers: retained old ASR bytes cannot enter a new build."""
from pathlib import Path
import shutil
import subprocess
import pytest


ROOT=Path(__file__).resolve().parents[1]


def test_explicit_library_is_required_before_real_composition(tmp_path):
    done=subprocess.run([shutil.which('cmake'),'-S',str(ROOT/'runtime/native/session'),'-B',str(tmp_path/'build'),'-DAII_SESSION_REAL_PROBE=ON'],capture_output=True,text=True,timeout=40)
    assert done.returncode!=0 and 'Explicit tested AII_ASR_LIBRARY required' in done.stderr


@pytest.mark.parametrize('missing', ['aii_asr_buffer_stats', 'aii_asr_create_configured', 'aii_asr_execution_info'])
def test_old_library_refused_and_reconfiguration_cannot_reuse_success(tmp_path, missing):
    # Real tiny shared library, so this test exercises the linker, not a text scan.
    compiler=shutil.which('c++');cmake=shutil.which('cmake');assert compiler and cmake
    source=tmp_path/'stub.cpp';library=tmp_path/'stub.dylib'
    definitions = {
        'aii_asr_buffer_stats': 'extern "C" int aii_asr_buffer_stats(void*,void*,char*,unsigned long){return 0;}\n',
        'aii_asr_create_configured': 'extern "C" void* aii_asr_create_configured(const char*,const float*,unsigned long,int,const char*,char*,unsigned long){return nullptr;}\n',
        'aii_asr_execution_info': 'extern "C" int aii_asr_execution_info(void*,char*,unsigned long,unsigned long*,char*,unsigned long){return 0;}\n',
    }
    source.write_text(''.join(definitions.values()))
    subprocess.run([compiler,'-shared','-fPIC',str(source),'-o',str(library)],check=True,capture_output=True,timeout=30)
    project=tmp_path/'project';project.mkdir()
    (project/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.22)\nproject(abi LANGUAGES CXX)\ninclude("'+str(ROOT/'runtime/native/session/require_asr.cmake')+'")\n')
    command=[cmake,'-S',str(project),'-B',str(tmp_path/'build'),'-DAII_ASR_LIBRARY='+str(library)]
    valid=subprocess.run(command,capture_output=True,text=True,timeout=40)
    assert valid.returncode==0,valid.stdout+valid.stderr
    source.write_text(''.join(body for name, body in definitions.items() if name != missing))
    subprocess.run([compiler,'-shared','-fPIC',str(source),'-o',str(library)],check=True,capture_output=True,timeout=30)
    invalid=subprocess.run(command,capture_output=True,text=True,timeout=40)
    diagnostic = ' '.join(invalid.stderr.split())
    assert invalid.returncode!=0 and 'lacks the required buffer-accounting/configuration/readback' in diagnostic
