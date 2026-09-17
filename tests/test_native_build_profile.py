"""The default single-config native build must not ship an unoptimized wrapper."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import pytest

from tests.conftest import cmake_usable

if not cmake_usable():
    pytest.skip('cmake on PATH does not run; the build profile is a real configure', allow_module_level=True)


class NativeBuildProfile(unittest.TestCase):
    def test_default_release_and_explicit_debug_are_both_honored(self):
        cmake=shutil.which('cmake')
        self.assertIsNotNone(cmake,'CMake is required for the native build contract')
        source=Path(__file__).resolve().parents[1]/'runtime/native/session'
        for requested,expected in ((None,'Release'),('', 'Release'),('Debug','Debug'),('RelWithDebInfo','RelWithDebInfo')):
            with self.subTest(requested=requested), tempfile.TemporaryDirectory(prefix='aii-native-profile-') as output:
                command=[cmake,'-G','Ninja','-S',str(source),'-B',output]
                if requested is not None:command+=['-DCMAKE_BUILD_TYPE='+requested]
                done=subprocess.run(command,capture_output=True,text=True,timeout=45)
                self.assertEqual(done.returncode,0,done.stdout+done.stderr)
                lines=(Path(output)/'CMakeCache.txt').read_text().splitlines()
                self.assertIn('CMAKE_BUILD_TYPE:STRING='+expected,lines)


if __name__=='__main__':unittest.main()
