"""The Linux Pocket link must reject missing generated shader definitions."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PocketLinkClosure(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux ELF linker proof')
    def test_unresolved_dependency_fails_the_actual_declared_link(self):
        text = (ROOT/'runtime/native_pocket/portable/CMakeLists.txt').read_text()
        flags = re.search(r'target_link_options\(native_pocket_resident PRIVATE "([^"]+)"\)', text).group(1)
        with tempfile.TemporaryDirectory(prefix='pocket-link-closure-') as temp:
            root = Path(temp)
            source = root/'missing.cpp'
            source.write_text('extern int missing_generated_shader;\nextern "C" int probe() { return missing_generated_shader; }\n')
            command = ['c++', '-shared', '-fPIC', str(source), '-o', str(root/'probe.so')]
            admitted = subprocess.run(command, capture_output=True, timeout=30)
            self.assertEqual(admitted.returncode, 0, admitted.stderr.decode())
            rejected = subprocess.run(command+[flags], capture_output=True, timeout=30)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn(b'missing_generated_shader', rejected.stderr)
            self.assertIn(b'undefined', rejected.stderr)


if __name__ == '__main__':
    unittest.main()
