"""Portable export-list guard plus a falsifier; not native Windows execution."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'runtime/native/session'
CM = shutil.which('cmake') or '/opt/homebrew/bin/cmake'


class WindowsExports(unittest.TestCase):
    def run_guard(self, exports):
        return subprocess.run([CM, '-DHEADER=' + str(ROOT / 'c_api.h'), '-DEXPORTS=' + str(exports),
            '-P', str(ROOT / 'check_windows_exports.cmake')], text=True, capture_output=True, timeout=10)

    def test_complete(self):
        result = self.run_guard(ROOT / 'native_runtime.def')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_policy_loader_is_refused_by_name(self):
        original = (ROOT / 'native_runtime.def').read_text()
        name = 'aii_voice_models_load_uid_policies'
        self.assertEqual(original.count(name), 1)
        with tempfile.TemporaryDirectory(prefix='voice-export-falsifier-') as directory:
            mutated = Path(directory) / 'exports.def'
            mutated.write_text(original.replace(name, ''))
            result = self.run_guard(mutated)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Windows native ABI export missing: ' + name, result.stderr)


if __name__ == '__main__':
    unittest.main()
