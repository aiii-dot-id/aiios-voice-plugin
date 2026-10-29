from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import pytest

from tests.conftest import unusable_cmake
from scripts.native_probe import compiler_env

BROKEN_CMAKE = unusable_cmake()
if BROKEN_CMAKE:
    pytest.skip(f'{BROKEN_CMAKE} does not run; the build profile is a real configure', allow_module_level=True)


class NativeBuildProfile(unittest.TestCase):
    def test_default_release_and_explicit_debug_are_both_honored(self):
        cmake=shutil.which('cmake')
        self.assertIsNotNone(cmake,'CMake is required for the native build contract')
        source=Path(__file__).resolve().parents[1]/'runtime/native/session'
        cases=((None,'Release'),('', 'Release'),('Debug','Debug'),('RelWithDebInfo','RelWithDebInfo'))
        for init,(requested,expected) in [(init,case) for init in (False,True) for case in cases]:
            with self.subTest(requested=requested,debug_init=init), tempfile.TemporaryDirectory(prefix='aii-native-profile-') as output:
                command=[cmake,'-G','Ninja','-S',str(source),'-B',output]
                if requested is not None:command+=['-DCMAKE_BUILD_TYPE='+requested]
                if init:
                    toolchain=Path(output)/'debug-init.cmake'
                    toolchain.write_text('set(CMAKE_BUILD_TYPE_INIT Debug)\n',encoding='utf-8',newline='\n')
                    command+=['-DCMAKE_TOOLCHAIN_FILE='+toolchain.as_posix()]
                done=subprocess.run(command,capture_output=True,text=True,timeout=45,env=compiler_env())
                self.assertEqual(done.returncode,0,done.stdout+done.stderr)
                lines=(Path(output)/'CMakeCache.txt').read_text().splitlines()
                self.assertIn('CMAKE_BUILD_TYPE:STRING='+expected,lines)


if __name__=='__main__':unittest.main()
