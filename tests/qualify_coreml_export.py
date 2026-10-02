"""Explicit Torch conversion semantics, no models or runtime fallback."""
from types import SimpleNamespace
import unittest
import torch
from scripts.export_coreml_separator import coreml_copy, trace_operations


class Unused(torch.nn.Module):
    pass


class CoreMLExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.manual_seed(28); torch.set_num_threads(4)

    def test_convolution_bits_and_owner_unchanged(self):
        original = torch.nn.Sequential(torch.nn.Conv2d(4, 4, (3, 1), padding=(1, 0), groups=4)).eval()
        converted, counts = coreml_copy(original, Unused, Unused)
        self.assertEqual(counts['convolutions'], 1)
        self.assertIsInstance(original[0], torch.nn.Conv2d)
        for key, weight in original.state_dict().items():
            torch.testing.assert_close(weight, converted.state_dict()[key], rtol=0, atol=0)
            self.assertNotEqual(weight.data_ptr(), converted.state_dict()[key].data_ptr())
        for n in (1, 31, 8999):
            x = torch.randn(1, 4, n, 1)
            torch.testing.assert_close(original(x), converted(x), rtol=1e-6, atol=1e-6)

    def test_other_convolution_refused(self):
        with self.assertRaisesRegex(ValueError, 'width-one'):
            coreml_copy(torch.nn.Sequential(torch.nn.Conv2d(4, 4, (3, 3))), Unused, Unused)

    def test_hierarchical_norm(self):
        for groups in (1, 4):
            for affine in (False, True):
                original = torch.nn.Sequential(torch.nn.GroupNorm(groups, 8, affine=affine)).eval()
                converted, counts = coreml_copy(original, Unused, Unused)
                self.assertEqual(counts['group_norms'], 1)
                self.assertIsInstance(original[0], torch.nn.GroupNorm)
                for n in (1, 31, 8999):
                    x = torch.randn(2, 8, n)*.03+.1
                    torch.testing.assert_close(original(x), converted(x), rtol=3e-5, atol=3e-5)

    def test_scoped_pad_semantics_and_failure_restoration(self):
        rotary = SimpleNamespace(einsum=torch.einsum); blocks = SimpleNamespace(einsum=torch.einsum)
        pad = torch.nn.functional.pad; x = torch.randn(2, 8, 32)
        for sizes, mode in (((0, 8), 'constant'), ((0, -1), 'constant'),
                ((0, 0, 1, -1), 'constant'), ((0, 2), 'reflect')):
            expected = pad(x, sizes, mode)
            with trace_operations(rotary, blocks):
                torch.testing.assert_close(torch.nn.functional.pad(x, sizes, mode), expected, rtol=0, atol=0)
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            with trace_operations(rotary, blocks):
                raise RuntimeError('injected')
        self.assertIs(torch.nn.functional.pad, pad)
        self.assertIs(rotary.einsum, torch.einsum); self.assertIs(blocks.einsum, torch.einsum)

    def test_attention_and_rotary_equivalence(self):
        rotary = SimpleNamespace(einsum=torch.einsum); blocks = SimpleNamespace(einsum=torch.einsum)
        cases = [ ('... i d, ... j d -> ... i j', torch.randn(2, 3, 5), torch.randn(2, 4, 5)),
            ('... i j, ... j d -> ... i d', torch.randn(2, 3, 4), torch.randn(2, 4, 5)),
            ('b g n d, b g n e -> b d e', torch.randn(2, 3, 4, 5), torch.randn(2, 3, 4, 6)),
            ('b g n d, b d e -> b g n e', torch.randn(2, 3, 4, 5), torch.randn(2, 5, 6))]
        with trace_operations(rotary, blocks):
            for equation, a, b in cases:
                torch.testing.assert_close(blocks.einsum(equation, a, b), torch.einsum(equation, a, b), rtol=1e-5, atol=1e-6)
            a, b = torch.randn(8), torch.randn(4)
            torch.testing.assert_close(rotary.einsum('..., f -> ... f', a, b), torch.einsum('..., f -> ... f', a, b), rtol=0, atol=0)


if __name__ == '__main__': unittest.main()
