"""Real link check: an old Linux adapter cannot silently lose device readback."""
from pathlib import Path
import ctypes
import shutil
import subprocess

import pytest

from tests.conftest import unusable_cmake

BROKEN_CMAKE = unusable_cmake()
if BROKEN_CMAKE:
    pytest.skip(f'{BROKEN_CMAKE} does not run; this link check configures a real project', allow_module_level=True)


ROOT = Path(__file__).resolve().parents[1]


def test_profile_export_builds_and_resolves_on_portable_runtime(tmp_path):
    compiler = shutil.which('c++')
    assert compiler, 'a C++ compiler is required for the real export proof'
    library = tmp_path/'profile.so'
    built = subprocess.run([compiler, '-std=c++17', '-shared', '-fPIC',
        str(ROOT/'runtime/native_pocket/profiling.cpp'), '-o', str(library)],
        capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stdout+built.stderr
    snapshot = ctypes.CDLL(str(library)).nv_profile_snapshot
    snapshot.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t, ctypes.c_int]
    snapshot.restype = ctypes.c_int
    counters = (ctypes.c_uint64 * 4)()
    assert snapshot(counters, 4, 0) == 0
    assert list(counters) == [0]*4
    assert snapshot(counters, 3, 0) != 0
    assert snapshot(counters, 4, 2) != 0


def test_readback_is_required_even_after_cached_success(tmp_path):
    compiler=shutil.which('c++'); cmake=shutil.which('cmake')
    assert compiler and cmake
    source=tmp_path/'adapter.cpp'; library=tmp_path/'adapter.so'
    def build(text):
        source.write_text(text)
        subprocess.run([compiler,'-shared','-fPIC',str(source),'-o',str(library)],
                       check=True,capture_output=True,timeout=30)
    build('#include <cstddef>\nextern "C" int nv_execution_info(void*,char*,std::size_t) noexcept {return -1;}\n')
    project=tmp_path/'project';project.mkdir()
    (project/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.22)\nproject(abi LANGUAGES CXX)\ninclude("'+str(ROOT/'runtime/native/session/require_pocket.cmake')+'")\n')
    command=[cmake,'-S',str(project),'-B',str(tmp_path/'build'),'-DAII_POCKET_LIBRARY='+str(library)]
    good=subprocess.run(command,capture_output=True,text=True,timeout=40)
    assert good.returncode==0,good.stdout+good.stderr
    build('extern "C" void* nv_create() {return nullptr;}\n')
    bad=subprocess.run(command,capture_output=True,text=True,timeout=40)
    assert bad.returncode!=0
    assert 'lacks the required Linux device-placement readback ABI' in ' '.join(bad.stderr.split())
