"""The session's selected UID implementation and its reported provider agree."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BackendBinding(unittest.TestCase):
    def test_native_selection_and_conflicts(self):
        compiler = shutil.which(os.environ.get('CXX', 'c++'))
        if compiler is None:
            self.fail('C++ compiler required for the UID backend contract')
        cases = [
            ([], 'cpu CPUExecutionProvider'),
            (['AII_SESSION_UID_BACKEND="cuda"'], 'cuda CUDAExecutionProvider'),
            (['AII_SESSION_UID_BACKEND="coreml-ane"'], 'coreml-ane CoreMLExecutionProvider'),
            (['AII_UID_NCNN'], 'ncnn-vulkan ncnn-vulkan'),
            (['AII_MOBILE_COREML_CANDIDATE'], 'coreml-ane CoreMLExecutionProvider'),
            (['AII_SESSION_UID_BACKEND="invented"'], None),
            (['AII_UID_NCNN', 'AII_SESSION_UID_BACKEND="cpu"'], None),
            (['AII_MOBILE_COREML_CANDIDATE', 'AII_SESSION_UID_BACKEND="cuda"'], None),
            (['AII_MOBILE_COREML_CANDIDATE', 'AII_UID_NCNN'], None),
        ]
        with tempfile.TemporaryDirectory(prefix='uid-backend-') as directory:
            source = Path(directory) / 'probe.cpp'
            source.write_text('#include "uid_backend.h"\n#include <iostream>\n'
                              'int main(){std::cout << aii::voice::uid_backend << " " '
                              '<< aii::voice::uid_provider;}\n')
            binary = Path(directory) / 'probe'
            for defines, expected in cases:
                with self.subTest(defines=defines):
                    result = subprocess.run([compiler, '-std=c++17', '-I',
                        str(ROOT / 'runtime/native/session'), *['-D'+d for d in defines],
                        str(source), '-o', str(binary)], capture_output=True, text=True, timeout=30)
                    if expected is None:
                        self.assertNotEqual(result.returncode, 0)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(subprocess.check_output([str(binary)], text=True, timeout=5), expected)


if __name__ == '__main__':
    unittest.main()
