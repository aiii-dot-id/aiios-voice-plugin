from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PocketLinkClosure(unittest.TestCase):
    def test_unresolved_dependency_fails_the_actual_declared_link(self):
        if not sys.platform.startswith('linux'):
            return self.platform_link_refuses()
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

    def platform_link_refuses(self):
        from scripts import native_probe

        with tempfile.TemporaryDirectory(prefix='pocket-link-closure-') as temp:
            root = Path(temp)
            source = root/'missing.cpp'
            source.write_text('extern int missing_generated_shader;\nextern "C" int probe() { return missing_generated_shader; }\n', encoding='utf-8', newline='\n')
            with self.assertRaises(RuntimeError) as refused:
                native_probe.build(root, 'probe', [source], kind='shared')
            self.assertIn('missing_generated_shader', str(refused.exception))


if __name__ == '__main__':
    unittest.main()
