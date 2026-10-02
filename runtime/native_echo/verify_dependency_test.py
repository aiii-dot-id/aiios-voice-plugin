"""Dependency bytes, not a clean-looking Git status, authorize compilation."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from verify_dependency import REVISION, verify


class Binding(unittest.TestCase):
    def test_exact_bytes_and_untracked_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.cc"
            data = b"int example;\n"
            source.write_bytes(data)
            digest = hashlib.sha1(b"blob 13\0" + data).hexdigest()
            tree = f"100644 blob {digest}\tsource.cc\0".encode()
            with patch("verify_dependency.subprocess.check_output",
                       side_effect=[REVISION.encode(), b"", tree]):
                verify(root)
            source.write_bytes(b"changed bytes")
            with patch("verify_dependency.subprocess.check_output",
                       side_effect=[REVISION.encode(), b"", tree]):
                with self.assertRaisesRegex(ValueError, "bytes differ"):
                    verify(root)
            with patch("verify_dependency.subprocess.check_output",
                       side_effect=[REVISION.encode(), b"injected.cc\0"]):
                with self.assertRaisesRegex(ValueError, "untracked"):
                    verify(root)
            with patch("verify_dependency.subprocess.check_output",
                       return_value=b"different revision"):
                with self.assertRaisesRegex(ValueError, "revision"):
                    verify(root)


if __name__ == "__main__":
    unittest.main()
